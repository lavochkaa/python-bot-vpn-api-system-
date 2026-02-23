from aiogram.fsm.state import State, StatesGroup


class SubscriptionStates(StatesGroup):
    waiting_duration = State()
    waiting_plan_type = State()
    waiting_build = State()
    waiting_traffic = State()
    waiting_confirm = State()
    waiting_promo_choice = State()
    waiting_promo_code = State()
