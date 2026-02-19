from bot.db.models import BalanceLedger
from bot.repositories.base import BaseRepository


class BalanceLedgerRepository(BaseRepository[BalanceLedger]):
    model = BalanceLedger
