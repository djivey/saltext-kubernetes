"""
    :codeauthor: Jochen Breuer <jbreuer@suse.de>
"""

import logging
import logging.handlers

# pylint: disable=no-value-for-parameter
from contextlib import contextmanager
from unittest.mock import MagicMock
from unittest.mock import Mock
from unittest.mock import mock_open
from unittest.mock import patch

import pytest
from kubernetes.client import V1Container
from kubernetes.client import V1DeploymentSpec
from kubernetes.client import V1PodSpec
from kubernetes.client import V1PodTemplateSpec
from kubernetes.client.rest import ApiException
from salt.exceptions import CommandExecutionError
from salt.modules import config

from saltext.kubernetes.modules import kubernetesmod as kubernetes

# Configure logging
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

# Disable logging for tests
logging.disable(logging.CRITICAL)


@pytest.fixture(autouse=True)
def setup_test_environment():
    """Configure test environment setup and cleanup"""
    # Store existing handlers
    root_logger = logging.getLogger()
    existing_handlers = root_logger.handlers[:]

    # Remove all handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add a null handler during tests
    null_handler = logging.NullHandler()
    root_logger.addHandler(null_handler)

    yield

    # Cleanup
    root_logger.removeHandler(null_handler)

    # Restore original handlers
    for handler in existing_handlers:
        root_logger.addHandler(handler)


@pytest.fixture()
def configure_loader_modules():
    """
    Configure loader modules for tests.
    """
    return {
        config: {
            "__opts__": {
                "kubernetes.kubeconfig": "/home/testuser/.minikube/kubeconfig.cfg",
                "kubernetes.context": "minikube",
                "cachedir": "/tmp/salt-test-cache",
                "extension_modules": "",
                "file_client": "local",
            }
        },
        kubernetes: {
            "__salt__": {
                "config.option": config.option,
                "cp.cache_file": MagicMock(return_value="/tmp/mock_file"),
            },
            "__grains__": {},
            "__pillar__": {},
            "__opts__": {
                "cachedir": "/tmp/salt-test-cache",
                "extension_modules": "",
                "file_client": "local",
            },
            "__context__": {},
        },
    }


@contextmanager
def mock_kubernetes_library():
    """
    After fixing the bug in 1c821c0e77de58892c77d8e55386fac25e518c31,
    it caused kubernetes._cleanup() to get called for virtually every
    test, which blows up. This prevents that specific blow-up once
    """
    with patch("saltext.kubernetes.modules.kubernetesmod.kubernetes") as mock_kubernetes_lib:
        yield mock_kubernetes_lib


def test_nodes():
    """
    Test node listing.
    :return:
    """
    with mock_kubernetes_library() as mock_kubernetes_lib:
        mock_kubernetes_lib.client.CoreV1Api.return_value = Mock(
            **{
                "list_node.return_value.to_dict.return_value": {
                    "items": [{"metadata": {"name": "mock_node_name"}}]
                }
            }
        )
        assert kubernetes.nodes() == ["mock_node_name"]
        assert kubernetes.kubernetes.client.CoreV1Api().list_node().to_dict.called


def test_deployments():
    """
    Tests deployment listing.
    :return:
    """
    with mock_kubernetes_library() as mock_kubernetes_lib:
        mock_kubernetes_lib.client.AppsV1Api.return_value = Mock(
            **{
                "list_namespaced_deployment.return_value.to_dict.return_value": {
                    "items": [{"metadata": {"name": "mock_deployment_name"}}]
                }
            }
        )
        assert kubernetes.deployments() == ["mock_deployment_name"]
        # py#int: disable=E1120
        assert kubernetes.kubernetes.client.AppsV1Api().list_namespaced_deployment().to_dict.called


def test_services():
    """
    Tests services listing.
    :return:
    """
    with mock_kubernetes_library() as mock_kubernetes_lib:
        mock_kubernetes_lib.client.CoreV1Api.return_value = Mock(
            **{
                "list_namespaced_service.return_value.to_dict.return_value": {
                    "items": [{"metadata": {"name": "mock_service_name"}}]
                }
            }
        )
        assert kubernetes.services() == ["mock_service_name"]
        assert kubernetes.kubernetes.client.CoreV1Api().list_namespaced_service().to_dict.called


def test_pods():
    """
    Tests pods listing.
    :return:
    """
    with mock_kubernetes_library() as mock_kubernetes_lib:
        mock_kubernetes_lib.client.CoreV1Api.return_value = Mock(
            **{
                "list_namespaced_pod.return_value.to_dict.return_value": {
                    "items": [{"metadata": {"name": "mock_pod_name"}}]
                }
            }
        )
        assert kubernetes.pods() == ["mock_pod_name"]
        assert kubernetes.kubernetes.client.CoreV1Api().list_namespaced_pod().to_dict.called


def test_delete_deployments():
    """
    Tests deployment deletion
    :return:
    """
    with mock_kubernetes_library() as mock_kubernetes_lib:
        with patch(
            "saltext.kubernetes.modules.kubernetesmod.show_deployment", Mock(return_value=None)
        ):
            mock_kubernetes_lib.client.V1DeleteOptions = Mock(return_value="")
            mock_kubernetes_lib.client.AppsV1Api.return_value = Mock(
                **{"delete_namespaced_deployment.return_value.to_dict.return_value": {"code": ""}}
            )
            assert kubernetes.delete_deployment("test") == {"code": 200}
            assert (
                kubernetes.kubernetes.client.AppsV1Api()
                .delete_namespaced_deployment()
                .to_dict.called
            )


def test_create_deployments():
    """
    Tests deployment creation.
    :return:
    """
    with mock_kubernetes_library() as mock_kubernetes_lib:
        mock_kubernetes_lib.client.V1DeploymentSpec = V1DeploymentSpec
        mock_kubernetes_lib.client.V1PodTemplateSpec = V1PodTemplateSpec
        mock_kubernetes_lib.client.V1PodSpec = V1PodSpec
        mock_kubernetes_lib.client.V1Container = V1Container
        mock_kubernetes_lib.client.AppsV1Api.return_value = Mock(
            **{"create_namespaced_deployment.return_value.to_dict.return_value": {}}
        )
        spec = {
            "template": {
                "metadata": {"labels": {"app": "test"}},
                "spec": {"containers": [{"name": "test-container", "image": "nginx"}]},
            },
            "selector": {"matchLabels": {"app": "test"}},
        }
        assert kubernetes.create_deployment("test", "default", {}, spec, None, None, None) == {}
        assert (
            kubernetes.kubernetes.client.AppsV1Api().create_namespaced_deployment().to_dict.called
        )


def test_setup_kubeconfig_file():
    """
    Test that the `kubernetes.kubeconfig` configuration isn't overwritten
    :return:
    """
    with mock_kubernetes_library() as mock_kubernetes_lib:
        mock_kubernetes_lib.config.load_kube_config = Mock()
        cfg = kubernetes._setup_conn()
        assert config.option("kubernetes.kubeconfig") == cfg["kubeconfig"]


def test_node_labels():
    """
    Test kubernetes.node_labels
    :return:
    """
    with patch("saltext.kubernetes.modules.kubernetesmod.node") as mock_node:
        mock_node.return_value = {
            "metadata": {
                "labels": {
                    "kubernetes.io/hostname": "minikube",
                    "kubernetes.io/os": "linux",
                }
            }
        }
        assert kubernetes.node_labels("minikube") == {
            "kubernetes.io/hostname": "minikube",
            "kubernetes.io/os": "linux",
        }


def test_adding_change_cause_annotation():
    """
    Tests adding a `kubernetes.io/change-cause` annotation just like
    kubectl [apply|create|replace] --record does
    :return:
    """
    with patch(
        "saltext.kubernetes.modules.kubernetesmod.sys.argv", ["/usr/bin/salt-call", "state.apply"]
    ):
        func = getattr(kubernetes, "__dict_to_object_meta")
        data = func(name="test-pod", namespace="test", metadata={})

        assert data.name == "test-pod"
        assert data.namespace == "test"
        assert data.annotations == {"kubernetes.io/change-cause": "/usr/bin/salt-call state.apply"}

        # Ensure any specified annotations aren't overwritten
        test_metadata = {"annotations": {"kubernetes.io/change-cause": "NOPE"}}
        data = func(name="test-pod", namespace="test", metadata=test_metadata)

        assert data.annotations == {"kubernetes.io/change-cause": "NOPE"}


def test_enforce_only_strings_dict():
    """
    Test conversion of dictionary values to strings.
    """
    func = getattr(kubernetes, "__enforce_only_strings_dict")
    data = {
        "unicode": 1,
        2: 2,
    }
    assert func(data) == {"unicode": "1", "2": "2"}


def test_create_deployment_with_context():
    """
    Test deployment creation with template context using actual YAML file
    """
    mock_template_data = {
        "result": True,
        "data": """apiVersion: apps/v1
kind: Deployment
metadata:
  name: test-deploy
spec:
  replicas: 3
  selector:
    matchLabels:
      app: test-deploy
  template:
    metadata:
      labels:
        app: test-deploy
    spec:
      containers:
      - name: test-deploy
        image: nginx:latest""",
    }

    mock_file_contents = MagicMock(return_value=mock_template_data["data"])

    with mock_kubernetes_library() as mock_kubernetes_lib:
        mock_kubernetes_lib.client.V1DeploymentSpec = V1DeploymentSpec
        mock_kubernetes_lib.client.V1PodTemplateSpec = V1PodTemplateSpec
        mock_kubernetes_lib.client.V1PodSpec = V1PodSpec
        mock_kubernetes_lib.client.V1Container = V1Container
        with (
            patch("salt.utils.files.fopen", mock_open(read_data=mock_file_contents())),
            patch(
                "salt.utils.templates.TEMPLATE_REGISTRY",
                {"jinja": MagicMock(return_value=mock_template_data)},
            ),
        ):
            context = {"name": "test-deploy", "replicas": 3, "image": "nginx:latest", "port": 80}
            mock_kubernetes_lib.client.AppsV1Api.return_value = Mock(
                **{"create_namespaced_deployment.return_value.to_dict.return_value": {}}
            )
            ret = kubernetes.create_deployment(
                "test-deploy",
                "default",
                {},
                {},
                "/mock/deployment.yaml",
                "jinja",
                "base",
                context=context,
            )
            assert ret == {}


def test_create_service_with_context():
    """
    Test service creation with template context using actual YAML file
    """
    template_content = """
apiVersion: v1
kind: Service
metadata:
  name: {{ context.name }}
spec:
  ports:
  - port: {{ context.port }}
    targetPort: {{ context.target_port }}
  type: {{ context.type }}
"""
    rendered_content = """
apiVersion: v1
kind: Service
metadata:
  name: test-svc
spec:
  ports:
  - port: 80
    targetPort: 8080
  type: LoadBalancer
"""
    mock_template_data = {"result": True, "data": rendered_content}

    mock_jinja = MagicMock(return_value=mock_template_data)
    template_registry = {"jinja": mock_jinja}

    with mock_kubernetes_library() as mock_kubernetes_lib:
        with (
            patch("salt.utils.files.fopen", mock_open(read_data=template_content)),
            patch("salt.utils.templates.TEMPLATE_REGISTRY", template_registry),
            patch(
                "salt.utils.yaml.safe_load",
                return_value={
                    "apiVersion": "v1",
                    "kind": "Service",
                    "metadata": {"name": "test-svc"},
                    "spec": {"ports": [{"port": 80, "targetPort": 8080}], "type": "LoadBalancer"},
                },
            ),
        ):

            context = {"name": "test-svc", "port": 80, "target_port": 8080, "type": "LoadBalancer"}
            mock_kubernetes_lib.client.CoreV1Api.return_value = Mock(
                **{"create_namespaced_service.return_value.to_dict.return_value": {}}
            )
            ret = kubernetes.create_service(
                "test-svc",
                "default",
                {},
                {},
                "/mock/service.yaml",
                "jinja",
                "base",
                context=context,
            )
            assert ret == {}

            mock_jinja.assert_called_once()
            call_kwargs = mock_jinja.call_args[1]
            assert call_kwargs.get("context") == context

            assert "port: 80" in rendered_content
            assert "targetPort: 8080" in rendered_content
            assert "type: LoadBalancer" in rendered_content


def test_replicasets():
    """
    Tests replicaset listing.
    """
    with mock_kubernetes_library() as mock_kubernetes_lib:
        mock_kubernetes_lib.client.AppsV1Api.return_value = Mock(
            **{
                "list_namespaced_replica_set.return_value.to_dict.return_value": {
                    "items": [{"metadata": {"name": "mock_replicaset_name"}}]
                }
            }
        )
        assert kubernetes.replicasets() == ["mock_replicaset_name"]
        assert kubernetes.kubernetes.client.AppsV1Api().list_namespaced_replica_set().to_dict.called


def test_delete_replicaset():
    """
    Tests replicaset deletion
    """
    with mock_kubernetes_library() as mock_kubernetes_lib:
        with patch(
            "saltext.kubernetes.modules.kubernetesmod.show_replicaset", Mock(return_value=None)
        ):
            mock_kubernetes_lib.client.V1DeleteOptions = Mock(return_value="")
            mock_kubernetes_lib.client.AppsV1Api.return_value = Mock(
                **{"delete_namespaced_replica_set.return_value.to_dict.return_value": {"code": ""}}
            )
            assert kubernetes.delete_replicaset("test") == {"code": 200}
            assert (
                kubernetes.kubernetes.client.AppsV1Api()
                .delete_namespaced_replica_set()
                .to_dict.called
            )


def test_create_replicaset_with_template():
    """
    Test replicaset creation with template and context
    """
    template_data = """apiVersion: apps/v1
kind: ReplicaSet
metadata:
  name: test-rs
spec:
  replicas: {{ context.replicas }}
  selector:
    matchLabels:
      app: test-rs
  template:
    metadata:
      labels:
        app: test-rs
    spec:
      containers:
      - name: test-rs
        image: {{ context.image }}"""

    rendered_data = """apiVersion: apps/v1
kind: ReplicaSet
metadata:
  name: test-rs
spec:
  replicas: 3
  selector:
    matchLabels:
      app: test-rs
  template:
    metadata:
      labels:
        app: test-rs
    spec:
      containers:
      - name: test-rs
        image: nginx:latest"""

    mock_template_data = {"result": True, "data": rendered_data}

    context = {"replicas": 3, "image": "nginx:latest"}

    with mock_kubernetes_library() as mock_kubernetes_lib:
        with (
            patch("salt.utils.files.fopen", mock_open(read_data=template_data)),
            patch(
                "salt.utils.templates.TEMPLATE_REGISTRY",
                {"jinja": MagicMock(return_value=mock_template_data)},
            ),
            patch(
                "salt.utils.yaml.safe_load",
                return_value={
                    "apiVersion": "apps/v1",
                    "kind": "ReplicaSet",
                    "metadata": {"name": "test-rs"},
                    "spec": {
                        "replicas": 3,
                        "selector": {"matchLabels": {"app": "test-rs"}},
                        "template": {
                            "metadata": {"labels": {"app": "test-rs"}},
                            "spec": {"containers": [{"name": "test-rs", "image": "nginx:latest"}]},
                        },
                    },
                },
            ),
        ):
            # Set up proper mocks for kubernetes client classes
            mock_kubernetes_lib.client.V1ObjectMeta = MagicMock()
            mock_kubernetes_lib.client.V1Container = MagicMock()
            mock_kubernetes_lib.client.V1PodSpec = MagicMock()
            mock_kubernetes_lib.client.V1PodTemplateSpec = MagicMock()
            mock_kubernetes_lib.client.V1LabelSelector = MagicMock()
            mock_kubernetes_lib.client.V1ReplicaSetSpec = MagicMock()
            mock_kubernetes_lib.client.AppsV1Api.return_value = Mock(
                **{"create_namespaced_replica_set.return_value.to_dict.return_value": {}}
            )

            ret = kubernetes.create_replicaset(
                "test-rs",
                "default",
                {},
                {},
                "/mock/replicaset.yaml",
                "jinja",
                "base",
                context=context,
            )
            assert ret == {}

            # Verify template rendering was called with correct context
            template_mock = list(kubernetes.__salt__["cp.cache_file"].mock_calls)[0]
            assert template_mock.args[0] == "/mock/replicaset.yaml"


def test_persistent_volumes():
    """
    Test persistent_volumes listing
    """
    with mock_kubernetes_library() as mock_kubernetes_lib:
        mock_kubernetes_lib.client.CoreV1Api.return_value = Mock(
            **{
                "list_persistent_volume.return_value.to_dict.return_value": {
                    "items": [{"metadata": {"name": "mock_pv_name"}}]
                }
            }
        )
        assert kubernetes.persistent_volumes() == ["mock_pv_name"]
        assert kubernetes.kubernetes.client.CoreV1Api().list_persistent_volume().to_dict.called


def test_show_persistent_volume():
    """
    Test persistent volume detail retrieval
    """
    with mock_kubernetes_library() as mock_kubernetes_lib:
        mock_kubernetes_lib.client.CoreV1Api.return_value = Mock(
            **{
                "read_persistent_volume.return_value.to_dict.return_value": {
                    "metadata": {"name": "mock_pv_name"},
                    "spec": {"capacity": {"storage": "1Gi"}},
                }
            }
        )
        assert kubernetes.show_persistent_volume("mock_pv_name") == {
            "metadata": {"name": "mock_pv_name"},
            "spec": {"capacity": {"storage": "1Gi"}},
        }
        assert kubernetes.kubernetes.client.CoreV1Api().read_persistent_volume().to_dict.called


def test_delete_persistent_volume():
    """
    Test persistent volume deletion
    """
    with mock_kubernetes_library() as mock_kubernetes_lib:
        mock_kubernetes_lib.client.V1DeleteOptions = Mock(return_value="")
        mock_kubernetes_lib.client.CoreV1Api.return_value = Mock(
            **{"delete_persistent_volume.return_value.to_dict.return_value": {"message": "Deleted"}}
        )
        result = kubernetes.delete_persistent_volume("mock_pv_name")
        assert result == {"message": "Deleted"}
        assert kubernetes.kubernetes.client.CoreV1Api().delete_persistent_volume().to_dict.called


def test_create_persistent_volume():
    """
    Test persistent volume creation
    """
    spec = {
        "capacity": {"storage": "1Gi"},
        "access_modes": ["ReadWriteOnce"],
        "persistent_volume_reclaim_policy": "Retain",
        "nfs": {"path": "/share", "server": "nfs.example.com"},
    }

    with mock_kubernetes_library() as mock_kubernetes_lib:
        mock_kubernetes_lib.client.V1ObjectMeta = MagicMock()
        mock_kubernetes_lib.client.V1PersistentVolumeSpec = MagicMock()
        mock_kubernetes_lib.client.V1PersistentVolume = MagicMock()
        mock_kubernetes_lib.client.CoreV1Api.return_value = Mock(
            **{"create_persistent_volume.return_value.to_dict.return_value": {}}
        )

        result = kubernetes.create_persistent_volume("test-pv", spec)
        assert result == {}
        assert kubernetes.kubernetes.client.CoreV1Api().create_persistent_volume().to_dict.called


def test_create_persistent_volume_with_source():
    """
    Test persistent volume creation using YAML source file
    """
    mock_pv_yaml = """
apiVersion: v1
kind: PersistentVolume
metadata:
  name: test-pv
spec:
  capacity:
    storage: 1Gi
  access_modes:
    - ReadWriteOnce
  nfs:
    path: /share
    server: nfs.example.com
"""

    with mock_kubernetes_library() as mock_kubernetes_lib:
        with (
            patch("salt.utils.files.fopen", mock_open(read_data=mock_pv_yaml)),
            patch(
                "salt.utils.yaml.safe_load",
                return_value={
                    "kind": "PersistentVolume",
                    "spec": {
                        "capacity": {"storage": "1Gi"},
                        "access_modes": ["ReadWriteOnce"],
                        "nfs": {"path": "/share", "server": "nfs.example.com"},
                    },
                },
            ),
        ):
            mock_kubernetes_lib.client.V1ObjectMeta = MagicMock()
            mock_kubernetes_lib.client.V1PersistentVolumeSpec = MagicMock()
            mock_kubernetes_lib.client.V1PersistentVolume = MagicMock()
            mock_kubernetes_lib.client.CoreV1Api.return_value = Mock(
                **{"create_persistent_volume.return_value.to_dict.return_value": {}}
            )

            result = kubernetes.create_persistent_volume(
                name="test-pv", spec={}, source="/mock/pv.yaml"
            )
            assert result == {}
            assert (
                kubernetes.kubernetes.client.CoreV1Api().create_persistent_volume().to_dict.called
            )


def test_persistent_volume_claims():
    """Test persistent_volume_claims listing"""
    with mock_kubernetes_library() as mock_kubernetes_lib:
        mock_kubernetes_lib.client.CoreV1Api.return_value = Mock(
            **{
                "list_namespaced_persistent_volume_claim.return_value.to_dict.return_value": {
                    "items": [{"metadata": {"name": "mock_pvc_name"}}]
                }
            }
        )
        assert kubernetes.persistent_volume_claims() == ["mock_pvc_name"]
        assert (
            kubernetes.kubernetes.client.CoreV1Api()
            .list_namespaced_persistent_volume_claim()
            .to_dict.called
        )


def test_show_persistent_volume_claim():
    """Test persistent_volume_claim detail retrieval"""
    with mock_kubernetes_library() as mock_kubernetes_lib:
        mock_kubernetes_lib.client.CoreV1Api.return_value = Mock(
            **{
                "read_namespaced_persistent_volume_claim.return_value.to_dict.return_value": {
                    "metadata": {"name": "mock_pvc_name"},
                    "spec": {
                        "access_modes": ["ReadWriteOnce"],
                        "resources": {"requests": {"storage": "1Gi"}},
                    },
                }
            }
        )
        assert kubernetes.show_persistent_volume_claim("mock_pvc_name") == {
            "metadata": {"name": "mock_pvc_name"},
            "spec": {
                "access_modes": ["ReadWriteOnce"],
                "resources": {"requests": {"storage": "1Gi"}},
            },
        }
        assert (
            kubernetes.kubernetes.client.CoreV1Api()
            .read_namespaced_persistent_volume_claim()
            .to_dict.called
        )


def test_create_persistent_volume_claim():
    """Test persistent_volume_claim creation"""
    spec = {
        "access_modes": ["ReadWriteOnce"],
        "resources": {"requests": {"storage": "1Gi"}},
    }

    with mock_kubernetes_library() as mock_kubernetes_lib:
        mock_kubernetes_lib.client.V1ObjectMeta = MagicMock()
        mock_kubernetes_lib.client.V1PersistentVolumeClaimSpec = MagicMock()
        mock_kubernetes_lib.client.V1PersistentVolumeClaim = MagicMock()
        mock_kubernetes_lib.client.CoreV1Api.return_value = Mock(
            **{"create_namespaced_persistent_volume_claim.return_value.to_dict.return_value": {}}
        )

        result = kubernetes.create_persistent_volume_claim(name="test-pvc", spec=spec)
        assert result == {}
        assert (
            kubernetes.kubernetes.client.CoreV1Api()
            .create_namespaced_persistent_volume_claim()
            .to_dict.called
        )


def test_delete_persistent_volume_claim():
    """Test persistent_volume_claim deletion"""
    with mock_kubernetes_library() as mock_kubernetes_lib:
        mock_kubernetes_lib.client.V1DeleteOptions = Mock(return_value="")
        mock_kubernetes_lib.client.CoreV1Api.return_value = Mock(
            **{
                "delete_namespaced_persistent_volume_claim.return_value.to_dict.return_value": {
                    "message": "Deleted"
                }
            }
        )
        result = kubernetes.delete_persistent_volume_claim("test-pvc")
        assert result == {"message": "Deleted"}
        assert (
            kubernetes.kubernetes.client.CoreV1Api()
            .delete_namespaced_persistent_volume_claim()
            .to_dict.called
        )


def test_replace_persistent_volume_claim():
    """Test persistent_volume_claim replacement with immutability checks"""
    name = "test-pvc"
    namespace = "default"

    existing_spec = {
        "access_modes": ["ReadWriteOnce"],
        "resources": {"requests": {"storage": "1Gi"}},
    }
    new_spec = {
        "resources": {"requests": {"storage": "2Gi"}},  # Only changing mutable field
    }

    # Create mock existing PVC object with proper structure
    mock_existing_pvc = MagicMock()
    mock_existing_pvc.spec = MagicMock(to_dict=lambda: existing_spec)
    mock_existing_pvc.metadata = MagicMock(resource_version="123")

    mock_api_instance = MagicMock()
    mock_api_instance.read_namespaced_persistent_volume_claim.return_value = mock_existing_pvc
    mock_api_instance.replace_namespaced_persistent_volume_claim.return_value = MagicMock(
        to_dict=lambda: {"metadata": {"name": name}}
    )

    with mock_kubernetes_library() as mock_kubernetes_lib:
        mock_kubernetes_lib.client.CoreV1Api.return_value = mock_api_instance
        mock_kubernetes_lib.client.V1ObjectMeta = MagicMock()
        mock_kubernetes_lib.client.V1PersistentVolumeClaimSpec = MagicMock()
        mock_kubernetes_lib.client.V1PersistentVolumeClaim = MagicMock()

        result = kubernetes.replace_persistent_volume_claim(name, namespace, spec=new_spec)
        assert result == {"metadata": {"name": name}}


def test_replace_persistent_volume_claim_immutable_field():
    """Test PVC replacement fails when modifying immutable fields"""
    name = "test-pvc"
    namespace = "default"

    existing_spec = {
        "access_modes": ["ReadWriteOnce"],
        "resources": {"requests": {"storage": "1Gi"}},
    }
    new_spec = {
        "access_modes": ["ReadWriteMany"],  # Trying to modify immutable field
        "resources": {"requests": {"storage": "1Gi"}},
    }

    # Create mock existing PVC object with proper structure
    mock_existing_pvc = MagicMock()
    mock_existing_pvc.spec = MagicMock(to_dict=lambda: existing_spec)

    mock_api_instance = MagicMock()
    mock_api_instance.read_namespaced_persistent_volume_claim.return_value = mock_existing_pvc
    mock_api_instance.replace_namespaced_persistent_volume_claim.side_effect = ApiException(
        status=422, reason="Invalid request: immutable field change"
    )

    with mock_kubernetes_library() as mock_kubernetes_lib:
        mock_kubernetes_lib.client.CoreV1Api.return_value = mock_api_instance
        mock_kubernetes_lib.client.V1ObjectMeta = MagicMock()
        mock_kubernetes_lib.client.V1PersistentVolumeClaimSpec = MagicMock()
        mock_kubernetes_lib.client.V1PersistentVolumeClaim = MagicMock()

        with pytest.raises((CommandExecutionError, ApiException)) as exc:
            kubernetes.replace_persistent_volume_claim(name, namespace, spec=new_spec)
        assert "Invalid request: immutable field change" in str(exc.value)


def test_validate_persistent_volume_claim_spec():
    """Test PVC spec validation"""
    # Test valid spec
    valid_spec = {
        "access_modes": ["ReadWriteOnce"],
        "resources": {"requests": {"storage": "1Gi"}},
    }
    result = kubernetes.__validate_persistent_volume_claim_spec(valid_spec)
    assert result == valid_spec

    # Test missing required fields
    with pytest.raises(CommandExecutionError) as exc:
        kubernetes.__validate_persistent_volume_claim_spec({})
    assert "spec.resources is required" in str(exc.value)

    with pytest.raises(CommandExecutionError) as exc:
        kubernetes.__validate_persistent_volume_claim_spec({"resources": {}})
    assert "spec.resources.requests is required" in str(exc.value)

    with pytest.raises(CommandExecutionError) as exc:
        kubernetes.__validate_persistent_volume_claim_spec({"resources": {"requests": {}}})
    assert "spec.resources.requests.storage is required" in str(exc.value)

    # Test invalid access modes
    invalid_spec = {
        "access_modes": ["InvalidMode"],
        "resources": {"requests": {"storage": "1Gi"}},
    }
    with pytest.raises(CommandExecutionError) as exc:
        kubernetes.__validate_persistent_volume_claim_spec(invalid_spec)
    assert "Invalid access mode" in str(exc.value)
