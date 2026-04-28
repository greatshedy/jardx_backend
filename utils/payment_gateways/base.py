from abc import ABC, abstractmethod

class BasePaymentGateway(ABC):
    @abstractmethod
    async def initialize_payment(self, amount: float, user_email: str, reference: str, **kwargs):
        """
        Initialize a payment and return the checkout URL.
        """
        pass

    @abstractmethod
    async def verify_transaction(self, reference: str):
        """
        Verify the status of a transaction with the provider.
        """
        pass

    @abstractmethod
    async def handle_webhook(self, payload: dict, signature: str, raw_body: bytes = None):
        """
        Handle and verify incoming webhooks from the provider.
        """
        pass
