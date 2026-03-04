from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
from typing import Literal


Backend = Literal["nft", "iptables"]


class FirewallManager:
    def __init__(self, backend: str = "auto") -> None:
        self.backend: Backend = self._detect_backend(backend)
        self._nft_ready = False
        self._iptables_ready_v4 = False
        self._iptables_ready_v6 = False

    def _detect_backend(self, backend: str) -> Backend:
        if backend == "nft":
            return "nft"
        if backend == "iptables":
            return "iptables"
        if shutil.which("nft"):
            return "nft"
        return "iptables"

    @staticmethod
    def _validate_ip(ip: str) -> ipaddress._BaseAddress:
        return ipaddress.ip_address(ip)

    @staticmethod
    def _validate_ports(ports: list[int]) -> list[int]:
        valid: list[int] = []
        for port in ports:
            if not isinstance(port, int):
                raise ValueError("Port must be int")
            if not (1 <= port <= 65535):
                raise ValueError("Port out of range")
            valid.append(port)
        if not valid:
            raise ValueError("At least one port is required")
        return sorted(set(valid))

    @staticmethod
    def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(cmd, check=False, capture_output=True, text=True)

    def add_block(self, ip: str, ports: list[int], ttl: int, block_udp: bool = True) -> None:
        _ = ttl
        ip_obj = self._validate_ip(ip)
        valid_ports = self._validate_ports(ports)
        if self.backend == "nft":
            self._nft_add_block(ip_obj, valid_ports, block_udp=block_udp)
            return
        self._iptables_add_block(ip_obj, valid_ports, block_udp=block_udp)

    def remove_block(self, ip: str, ports: list[int], block_udp: bool = True) -> None:
        ip_obj = self._validate_ip(ip)
        valid_ports = self._validate_ports(ports)
        if self.backend == "nft":
            self._nft_remove_block(ip_obj, valid_ports, block_udp=block_udp)
            return
        self._iptables_remove_block(ip_obj, valid_ports, block_udp=block_udp)

    def _nft_ensure_ready(self) -> None:
        if self._nft_ready:
            return

        if self._run(["nft", "list", "table", "inet", "devlimit"]).returncode != 0:
            self._run(["nft", "add", "table", "inet", "devlimit"])

        if self._run(["nft", "list", "chain", "inet", "devlimit", "input"]).returncode != 0:
            self._run(
                [
                    "nft",
                    "add",
                    "chain",
                    "inet",
                    "devlimit",
                    "input",
                    "{",
                    "type",
                    "filter",
                    "hook",
                    "input",
                    "priority",
                    "0",
                    ";",
                    "policy",
                    "accept",
                    ";",
                    "}",
                ]
            )

        self._nft_ready = True

    @staticmethod
    def _nft_port_set(ports: list[int]) -> str:
        return "{ " + ", ".join(str(p) for p in ports) + " }"

    def _nft_has_comment(self, comment: str) -> bool:
        listed = self._run(["nft", "-a", "list", "chain", "inet", "devlimit", "input"])
        return listed.returncode == 0 and comment in listed.stdout

    def _nft_add_block(self, ip: ipaddress._BaseAddress, ports: list[int], block_udp: bool = True) -> None:
        self._nft_ensure_ready()
        family_key = "ip6" if ip.version == 6 else "ip"
        port_set = self._nft_port_set(ports)

        comment_tcp = f"devlimit:tcp:{ip}"
        if not self._nft_has_comment(comment_tcp):
            self._run(
                [
                    "nft",
                    "add",
                    "rule",
                    "inet",
                    "devlimit",
                    "input",
                    family_key,
                    "saddr",
                    str(ip),
                    "tcp",
                    "dport",
                    port_set,
                    "drop",
                    "comment",
                    comment_tcp,
                ]
            )

        if block_udp:
            comment_udp = f"devlimit:udp:{ip}"
            if not self._nft_has_comment(comment_udp):
                self._run(
                    [
                        "nft",
                        "add",
                        "rule",
                        "inet",
                        "devlimit",
                        "input",
                        family_key,
                        "saddr",
                        str(ip),
                        "udp",
                        "dport",
                        port_set,
                        "drop",
                        "comment",
                        comment_udp,
                    ]
                )

    def _nft_remove_by_comment(self, comment: str) -> None:
        listed = self._run(["nft", "-a", "list", "chain", "inet", "devlimit", "input"])
        if listed.returncode != 0:
            return
        handles = re.findall(rf'.*comment "{re.escape(comment)}".* handle (\d+)', listed.stdout)
        for handle in handles:
            self._run(["nft", "delete", "rule", "inet", "devlimit", "input", "handle", handle])

    def _nft_remove_block(self, ip: ipaddress._BaseAddress, ports: list[int], block_udp: bool = True) -> None:
        _ = ports
        self._nft_remove_by_comment(f"devlimit:tcp:{ip}")
        if block_udp:
            self._nft_remove_by_comment(f"devlimit:udp:{ip}")

    def _iptables_tool(self, ip: ipaddress._BaseAddress) -> str:
        return "ip6tables" if ip.version == 6 else "iptables"

    @staticmethod
    def _ports_csv(ports: list[int]) -> str:
        return ",".join(str(p) for p in ports)

    def _iptables_ensure_ready(self, ip_version: int, ports: list[int], block_udp: bool = True) -> None:
        if ip_version == 4 and self._iptables_ready_v4:
            return
        if ip_version == 6 and self._iptables_ready_v6:
            return

        tool = "iptables" if ip_version == 4 else "ip6tables"

        if self._run([tool, "-nL", "DEVLIMIT"]).returncode != 0:
            self._run([tool, "-N", "DEVLIMIT"])

        ports_csv = self._ports_csv(ports)
        tcp_jump = [tool, "-C", "INPUT", "-p", "tcp", "-m", "multiport", "--dports", ports_csv, "-j", "DEVLIMIT"]
        if self._run(tcp_jump).returncode != 0:
            self._run([tool, "-I", "INPUT", "-p", "tcp", "-m", "multiport", "--dports", ports_csv, "-j", "DEVLIMIT"])

        if block_udp:
            udp_jump = [tool, "-C", "INPUT", "-p", "udp", "-m", "multiport", "--dports", ports_csv, "-j", "DEVLIMIT"]
            if self._run(udp_jump).returncode != 0:
                self._run([tool, "-I", "INPUT", "-p", "udp", "-m", "multiport", "--dports", ports_csv, "-j", "DEVLIMIT"])

        if ip_version == 4:
            self._iptables_ready_v4 = True
        else:
            self._iptables_ready_v6 = True

    def _iptables_add_block(self, ip: ipaddress._BaseAddress, ports: list[int], block_udp: bool = True) -> None:
        tool = self._iptables_tool(ip)
        self._iptables_ensure_ready(ip.version, ports, block_udp=block_udp)
        check = [tool, "-C", "DEVLIMIT", "-s", str(ip), "-j", "DROP"]
        if self._run(check).returncode != 0:
            self._run([tool, "-A", "DEVLIMIT", "-s", str(ip), "-j", "DROP"])

    def _iptables_remove_block(self, ip: ipaddress._BaseAddress, ports: list[int], block_udp: bool = True) -> None:
        tool = self._iptables_tool(ip)
        self._iptables_ensure_ready(ip.version, ports, block_udp=block_udp)
        while self._run([tool, "-C", "DEVLIMIT", "-s", str(ip), "-j", "DROP"]).returncode == 0:
            self._run([tool, "-D", "DEVLIMIT", "-s", str(ip), "-j", "DROP"])
