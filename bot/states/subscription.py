from aiogram.fsm.state import State, StatesGroup


class SubscriptionStates(StatesGroup):
    waiting_promo_code = State()
