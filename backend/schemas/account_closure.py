from typing import Literal
from pydantic import BaseModel, StrictBool


class AccountClosureRequest(BaseModel):
    policy_version: Literal['normal-close-30d-v1']
    confirm: StrictBool


class RecoveryConfirmedResponse(BaseModel):
    login_required: Literal[True] = True
    message: str = 'Аккаунт восстановлен. Войдите с паролем и, если включена, двухфакторной проверкой.'
