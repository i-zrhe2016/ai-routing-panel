from .ai_routing import AiRoutingMixin
from .base import CoreMixin
from .commerce import CommerceMixin
from .dns_failover import DnsFailoverMixin
from .ports import PortsMixin
from .probes import ProbesMixin
from .traffic import TrafficMixin


class PanelState(
    CoreMixin,
    PortsMixin,
    TrafficMixin,
    ProbesMixin,
    DnsFailoverMixin,
    AiRoutingMixin,
    CommerceMixin,
):
    pass
