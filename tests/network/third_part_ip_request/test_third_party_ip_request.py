from collections.abc import Generator
from typing import Final

import pytest
from kubernetes.dynamic import DynamicClient
from ocp_resources.namespace import Namespace
from ocp_resources.virtual_machine_instance import VirtualMachineInstance

from libs.net.udn import create_udn_namespace
from libs.vm.factory import base_vmspec
from libs.vm.vm import BaseVirtualMachine
from tests.network.libs import cluster_user_defined_network as libcudn
from tests.network.libs.label_selector import LabelSelector
from utilities.constants import OS_FLAVOR_FEDORA

CUDN_LABEL: Final[dict] = {"cudn": "req-ip"}
IPV4_SUBNET_PREFIX: Final[str] = "172.16.100"
CUDN_SUBNET_IPV4: Final[str] = f"{IPV4_SUBNET_PREFIX}.0/24"
REQUESTED_IPV4: Final[str] = f"{IPV4_SUBNET_PREFIX}.50/24"
IP_REQUEST_ANNOTATION_KEY: Final[str] = "network.kubevirt.io/addresses"


@pytest.fixture(scope="module")
def cudn_namespace(admin_client: DynamicClient) -> Generator[Namespace]:
    yield from create_udn_namespace(name="req-ip-ns", client=admin_client, labels={**CUDN_LABEL})


@pytest.fixture(scope="module")
def requested_ip_range_primary_cudn(
    admin_client: DynamicClient, cudn_namespace: Namespace
) -> Generator[libcudn.ClusterUserDefinedNetwork]:
    with libcudn.ClusterUserDefinedNetwork(
        name="req-ip-cudn",
        namespace_selector=LabelSelector(matchLabels=CUDN_LABEL),
        network=libcudn.Network(
            topology=libcudn.Network.Topology.LAYER2.value,
            layer2=libcudn.Layer2(
                role=libcudn.Layer2.Role.PRIMARY.value,
                ipam=libcudn.Ipam(mode=libcudn.Ipam.Mode.ENABLED.value, lifecycle="Persistent"),
                subnets=[CUDN_SUBNET_IPV4],
            ),
        ),
        client=admin_client,
    ) as cudn:
        cudn.wait_for_status_success()
        yield cudn
        # teardown will fail if there are any pods attached to cudn_namespace, need to clean up the namespace first
        cudn_namespace.clean_up()


@pytest.fixture(scope="function")
def vm_with_requested_ip(
    admin_client: DynamicClient,
    cudn_namespace: Namespace,
    requested_ip_range_primary_cudn: libcudn.ClusterUserDefinedNetwork,
) -> Generator[BaseVirtualMachine]:
    vm_spec = base_vmspec()

    with BaseVirtualMachine(
        namespace=cudn_namespace.name,
        name="vm-req-ip",
        spec=vm_spec,
        os_distribution=OS_FLAVOR_FEDORA,
        vm_annotations={IP_REQUEST_ANNOTATION_KEY: f'["{REQUESTED_IPV4}"]'},
        client=admin_client,
    ) as vm:
        vm.start(wait=True)
        vm.vmi.wait_for_condition(
            condition=VirtualMachineInstance.Condition.Type.RUNNING,
            status=VirtualMachineInstance.Condition.Status.TRUE,
            timeout=300,
        )
        yield vm


@pytest.mark.polarion("CNV-12454")
def test_annotated_ip_assigned(
    vm_with_requested_ip: BaseVirtualMachine,
):
    vmi_interfaces = vm_with_requested_ip.vmi.instance.status.interfaces
    assert vmi_interfaces, "VMI has no interfaces in status"

    interface_ips = vmi_interfaces[0].get("ipAddresses", [])
    assert REQUESTED_IPV4 in interface_ips, (
        f"Requested IP {REQUESTED_IPV4} not found in VM's primary interface IPs. Found IPs: {interface_ips}"
    )
