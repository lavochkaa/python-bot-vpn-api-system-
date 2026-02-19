from aiogram.fsm.state import State, StatesGroup


class TopUpStates(StatesGroup):
    waiting_custom_amount = State()
    waiting_promo = State()
    waiting_promo_code = State()
