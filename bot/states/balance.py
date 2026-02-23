from aiogram.fsm.state import State, StatesGroup


class TopUpStates(StatesGroup):
    waiting_custom_amount = State()
