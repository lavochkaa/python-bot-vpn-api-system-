from aiogram.fsm.state import State, StatesGroup


class SupportStates(StatesGroup):
    waiting_message = State()
    waiting_reply_message = State()
