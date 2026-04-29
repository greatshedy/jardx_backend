from pydantic import BaseModel,EmailStr

class User(BaseModel):
    user_name:str=""
    wallet_balance:float=0.0
    email:EmailStr
    phone_number:str=""
    password:str=""
    otp:str=""
    transaction_pin:str=""
    referral_code: str = ""
    referred_by: str = ""
    is_referral_active: bool = False
    referral_percentage: float = 0.0
    referral_bonus_paid: bool = False


class Login(BaseModel):
    email:EmailStr
    password:str=""
    otp:str=""


class GoogleAuth(BaseModel):
    idToken: str
    platform: str = "web"


class House(BaseModel):
    house_name:str=""
    house_about:str=""
    house_pricing_plan:list=[dict]
    house_location:str=""
    house_image:list=[str]
    house_landmarks:list=[]
    house_benefits:list=[]
    house_status:str=""

class PropertyPurchase(BaseModel):
    house_id: str
    plan_type: str  # 'outright' or 'installment'
    plan_index: int = None  # to know which installment plan was picked
    amount_to_pay: float  # outright price or down_payment

class PortfolioModel(BaseModel):
    user_id: str
    house_id: str
    house_name: str
    plan_type: str
    total_price: float
    amount_paid: float
    remaining_balance: float
    monthly_payment: float = 0.0
    duration_months: int = 0
    months_paid: int = 0
    next_payment_date: str = ""
    status: str = "Active" # 'Active', 'Completed', 'Defaulted'
    created_at: str = ""

class JardKidzPlan(BaseModel):
    user_id: str = ""
    child_name: str
    child_dob: str
    child_gender: str
    monthly_amount: float
    total_months: int
    months_paid: int = 1
    investment_period_years: int
    return_percentage: float
    expected_return: float # total expected profit or total maturity value? Image shows #750,000 as expected return.
    plan_type: str = "child_savings" # 'child_savings' or 'child_property'
    status: str = "Active"
    created_at: str = ""

class ForgotPassword(BaseModel):
    email: EmailStr

class ResetPassword(BaseModel):
    email: EmailStr
    otp: str
    new_password: str
