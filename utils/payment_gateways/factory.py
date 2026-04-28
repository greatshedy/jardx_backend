from .monnify import MonnifyGateway
from .flutterwave import FlutterwaveGateway

class PaymentGatewayFactory:
    _gateways = {
        "Monnify": MonnifyGateway,
        "Flutterwave": FlutterwaveGateway
    }

    @classmethod
    def get_gateway(cls, provider_name: str):
        gateway_class = cls._gateways.get(provider_name)
        if not gateway_class:
            raise ValueError(f"Unsupported payment gateway: {provider_name}")
        return gateway_class()
