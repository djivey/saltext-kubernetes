"""
    :codeauthor: :email:`Jeff Schroeder <jeffschroeder@computer.org>`
"""

import base64
from contextlib import contextmanager
from unittest.mock import MagicMock
from unittest.mock import mock_open
from unittest.mock import patch

import pytest
import salt.utils.stringutils

from saltext.kubernetes.modules import kubernetesmod
from saltext.kubernetes.states import kubernetes

pytestmark = [
    pytest.mark.skipif(
        kubernetesmod.HAS_LIBS is False,
        reason="Kubernetes client lib is not installed.",
    )
]


@pytest.fixture
def configure_loader_modules():
    return {kubernetes: {"__env__": "base"}}


@contextmanager
def mock_func(func_name, return_value, test=False):
    """
    Mock any of the kubernetes state function return values and set
    the test options.
    """
    name = f"kubernetes.{func_name}"
    mocked = {name: MagicMock(return_value=return_value)}
    with patch.dict(kubernetes.__salt__, mocked) as patched:
        with patch.dict(kubernetes.__opts__, {"test": test}):
            yield patched


def make_configmap(name, namespace="default", data=None):
    return make_ret_dict(
        kind="ConfigMap",
        name=name,
        namespace=namespace,
        data=data,
    )


def make_secret(name, namespace="default", data=None):
    secret_data = make_ret_dict(
        kind="Secret",
        name=name,
        namespace=namespace,
        data=data,
    )
    # Base64 all of the values just like kubectl does
    for key, value in secret_data["data"].items():
        secret_data["data"][key] = base64.b64encode(salt.utils.stringutils.to_bytes(value))

    return secret_data


def make_node_labels(name="minikube"):
    return {
        "kubernetes.io/hostname": name,
        "beta.kubernetes.io/os": "linux",
        "beta.kubernetes.io/arch": "amd64",
        "failure-domain.beta.kubernetes.io/region": "us-west-1",
    }


def make_node(name="minikube"):
    node_data = make_ret_dict(kind="Node", name="minikube")
    node_data.update(
        {
            "api_version": "v1",
            "kind": "Node",
            "metadata": {
                "annotations": {"node.alpha.kubernetes.io/ttl": "0"},
                "labels": make_node_labels(name=name),
                "name": name,
                "namespace": None,
                "link": f"/api/v1/nodes/{name}",
                "uid": "7811b8ae-c1a1-11e7-a55a-0800279fb61e",
            },
            "spec": {"external_id": name},
            "status": {},
        }
    )
    return node_data


def make_namespace(name="default"):
    namespace_data = make_ret_dict(kind="Namespace", name=name)
    del namespace_data["data"]
    namespace_data.update(
        {
            "status": {"phase": "Active"},
            "spec": {"finalizers": ["kubernetes"]},
            "metadata": {
                "name": name,
                "namespace": None,
                "labels": None,
                "link": f"/api/v1/namespaces/{name}",
                "annotations": None,
                "uid": "752fceeb-c1a1-11e7-a55a-0800279fb61e",
            },
        }
    )
    return namespace_data


def make_ret_dict(kind, name, namespace=None, data=None):
    """
    Make a minimal example configmap or secret for using in mocks
    """

    assert kind in ("Secret", "ConfigMap", "Namespace", "Node")

    if data is None:
        data = {}

    link = f"/api/v1/namespaces/{namespace}/{kind.lower()}s/{name}"

    return_data = {
        "kind": kind,
        "data": data,
        "api_version": "v1",
        "metadata": {
            "name": name,
            "labels": None,
            "namespace": namespace,
            "link": link,
            "annotations": {"kubernetes.io/change-cause": "salt-call state.apply"},
        },
    }
    return return_data


def test_configmap_present__fail():
    error = kubernetes.configmap_present(
        name="testme",
        data={1: 1},
        source="salt://beyond/oblivion.jinja",
    )
    assert error == {
        "changes": {},
        "result": False,
        "name": "testme",
        "comment": "'source' cannot be used in combination with 'data'",
    }


def test_configmap_present__create_test_true():
    # Create a new configmap with test=True
    with mock_func("show_configmap", return_value=None, test=True):
        ret = kubernetes.configmap_present(
            name="example",
            data={"example.conf": "# empty config file"},
        )
        assert ret == {
            "comment": "The configmap is going to be created",
            "changes": {},
            "name": "example",
            "result": None,
        }


def test_configmap_present__create():
    # Create a new configmap
    with mock_func("show_configmap", return_value=None):
        cm = make_configmap(
            name="test",
            namespace="default",
            data={"foo": "bar"},
        )
        with mock_func("create_configmap", return_value=cm):
            actual = kubernetes.configmap_present(
                name="test",
                data={"foo": "bar"},
            )
            assert actual == {
                "comment": "",
                "changes": {"data": {"foo": "bar"}},
                "name": "test",
                "result": True,
            }


def test_configmap_present__create_no_data():
    # Create a new configmap with no 'data' attribute
    with mock_func("show_configmap", return_value=None):
        cm = make_configmap(
            name="test",
            namespace="default",
        )
        with mock_func("create_configmap", return_value=cm):
            actual = kubernetes.configmap_present(name="test")
            assert actual == {
                "comment": "",
                "changes": {"data": {}},
                "name": "test",
                "result": True,
            }


def test_configmap_present__replace_test_true():
    cm = make_configmap(
        name="settings",
        namespace="saltstack",
        data={"foobar.conf": "# Example configuration"},
    )
    with mock_func("show_configmap", return_value=cm, test=True):
        ret = kubernetes.configmap_present(
            name="settings",
            namespace="saltstack",
            data={"foobar.conf": "# Example configuration"},
        )
        assert ret == {
            "comment": "The configmap is going to be replaced",
            "changes": {},
            "name": "settings",
            "result": None,
        }


def test_configmap_present__replace():
    cm = make_configmap(name="settings", data={"action": "make=war"})
    # Replace an existing configmap
    with mock_func("show_configmap", return_value=cm):
        new_cm = cm.copy()
        new_cm.update({"data": {"action": "make=peace"}})
        with mock_func("replace_configmap", return_value=new_cm):
            actual = kubernetes.configmap_present(
                name="settings",
                data={"action": "make=peace"},
            )
            assert actual == {
                "comment": ("The configmap is already present. Forcing recreation"),
                "changes": {"data": {"action": "make=peace"}},
                "name": "settings",
                "result": True,
            }


def test_configmap_absent__noop_test_true():
    # Nothing to delete with test=True
    with mock_func("show_configmap", return_value=None, test=True):
        actual = kubernetes.configmap_absent(name="NOT_FOUND")
        assert actual == {
            "comment": "The configmap does not exist",
            "changes": {},
            "name": "NOT_FOUND",
            "result": None,
        }


def test_configmap_absent__test_true():
    # Configmap exists with test=True
    cm = make_configmap(name="deleteme", namespace="default")
    with mock_func("show_configmap", return_value=cm, test=True):
        actual = kubernetes.configmap_absent(name="deleteme")
        assert actual == {
            "comment": "The configmap is going to be deleted",
            "changes": {},
            "name": "deleteme",
            "result": None,
        }


def test_configmap_absent__noop():
    # Nothing to delete
    with mock_func("show_configmap", return_value=None):
        actual = kubernetes.configmap_absent(name="NOT_FOUND")
        assert actual == {
            "comment": "The configmap does not exist",
            "changes": {},
            "name": "NOT_FOUND",
            "result": True,
        }


def test_configmap_absent():
    # Configmap exists, delete it!
    cm = make_configmap(name="deleteme", namespace="default")
    with mock_func("show_configmap", return_value=cm):
        # The return from this module isn't used in the state
        with mock_func("delete_configmap", return_value={}):
            actual = kubernetes.configmap_absent(name="deleteme")
            assert actual == {
                "comment": "ConfigMap deleted",
                "changes": {
                    "kubernetes.configmap": {
                        "new": "absent",
                        "old": "present",
                    },
                },
                "name": "deleteme",
                "result": True,
            }


def test_secret_present__fail():
    actual = kubernetes.secret_present(
        name="sekret",
        data={"password": "monk3y"},
        source="salt://nope.jinja",
    )
    assert actual == {
        "changes": {},
        "result": False,
        "name": "sekret",
        "comment": "'source' cannot be used in combination with 'data'",
    }


def test_secret_present__exists_test_true():
    secret = make_secret(name="sekret")
    new_secret = secret.copy()
    new_secret.update({"data": {"password": "uncle"}})
    # Secret exists already and needs replacing with test=True
    with mock_func("show_secret", return_value=secret):
        with mock_func("replace_secret", return_value=new_secret, test=True):
            actual = kubernetes.secret_present(
                name="sekret",
                data={"password": "uncle"},
            )
            assert actual == {
                "changes": {},
                "result": None,
                "name": "sekret",
                "comment": "The secret is going to be replaced",
            }


def test_secret_present__exists():
    # Secret exists and gets replaced
    secret = make_secret(name="sekret", data={"password": "booyah"})
    with mock_func("show_secret", return_value=secret):
        with mock_func("replace_secret", return_value=secret):
            actual = kubernetes.secret_present(
                name="sekret",
                data={"password": "booyah"},
            )
            assert actual == {
                "changes": {"data": ["password"]},
                "result": True,
                "name": "sekret",
                "comment": "The secret is already present. Forcing recreation",
            }


def test_secret_present__create():
    # Secret exists and gets replaced
    secret = make_secret(name="sekret", data={"password": "booyah"})
    with mock_func("show_secret", return_value=None):
        with mock_func("create_secret", return_value=secret):
            actual = kubernetes.secret_present(
                name="sekret",
                data={"password": "booyah"},
            )
            assert actual == {
                "changes": {"data": ["password"]},
                "result": True,
                "name": "sekret",
                "comment": "",
            }


def test_secret_present__create_no_data():
    # Secret exists and gets replaced
    secret = make_secret(name="sekret")
    with mock_func("show_secret", return_value=None):
        with mock_func("create_secret", return_value=secret):
            actual = kubernetes.secret_present(name="sekret")
            assert actual == {
                "changes": {"data": []},
                "result": True,
                "name": "sekret",
                "comment": "",
            }


def test_secret_present__create_test_true():
    # Secret exists and gets replaced with test=True
    secret = make_secret(name="sekret")
    with mock_func("show_secret", return_value=None):
        with mock_func("create_secret", return_value=secret, test=True):
            actual = kubernetes.secret_present(name="sekret")
            assert actual == {
                "changes": {},
                "result": None,
                "name": "sekret",
                "comment": "The secret is going to be created",
            }


def test_secret_absent__noop_test_true():
    with mock_func("show_secret", return_value=None, test=True):
        actual = kubernetes.secret_absent(name="sekret")
        assert actual == {
            "changes": {},
            "result": None,
            "name": "sekret",
            "comment": "The secret does not exist",
        }


def test_secret_absent__noop():
    with mock_func("show_secret", return_value=None):
        actual = kubernetes.secret_absent(name="passwords")
        assert actual == {
            "changes": {},
            "result": True,
            "name": "passwords",
            "comment": "The secret does not exist",
        }


def test_secret_absent__delete_test_true():
    secret = make_secret(name="credentials", data={"redis": "letmein"})
    with mock_func("show_secret", return_value=secret):
        with mock_func("delete_secret", return_value=secret, test=True):
            actual = kubernetes.secret_absent(name="credentials")
            assert actual == {
                "changes": {},
                "result": None,
                "name": "credentials",
                "comment": "The secret is going to be deleted",
            }


def test_secret_absent__delete():
    secret = make_secret(name="foobar", data={"redis": "letmein"})
    deleted = {
        "status": None,
        "kind": "Secret",
        "code": None,
        "reason": None,
        "details": None,
        "message": None,
        "api_version": "v1",
        "metadata": {
            "link": "/api/v1/namespaces/default/secrets/foobar",
            "resource_version": "30292",
        },
    }
    with mock_func("show_secret", return_value=secret):
        with mock_func("delete_secret", return_value=deleted):
            actual = kubernetes.secret_absent(name="foobar")
            assert actual == {
                "changes": {
                    "kubernetes.secret": {"new": "absent", "old": "present"},
                },
                "result": True,
                "name": "foobar",
                "comment": "Secret deleted",
            }


def test_node_label_present__add_test_true():
    labels = make_node_labels()
    with mock_func("node_labels", return_value=labels, test=True):
        actual = kubernetes.node_label_present(
            name="com.zoo-animal",
            node="minikube",
            value="monkey",
        )
        assert actual == {
            "changes": {},
            "result": None,
            "name": "com.zoo-animal",
            "comment": "The label is going to be set",
        }


def test_node_label_present__add():
    node_data = make_node()
    # Remove some of the defaults to make it simpler
    node_data["metadata"]["labels"] = {
        "beta.kubernetes.io/os": "linux",
    }
    labels = node_data["metadata"]["labels"]

    with mock_func("node_labels", return_value=labels):
        with mock_func("node_add_label", return_value=node_data):
            actual = kubernetes.node_label_present(
                name="failure-domain.beta.kubernetes.io/zone",
                node="minikube",
                value="us-central1-a",
            )
            assert actual == {
                "comment": "",
                "changes": {
                    "minikube.failure-domain.beta.kubernetes.io/zone": {
                        "new": {
                            "failure-domain.beta.kubernetes.io/zone": ("us-central1-a"),
                            "beta.kubernetes.io/os": "linux",
                        },
                        "old": {"beta.kubernetes.io/os": "linux"},
                    },
                },
                "name": "failure-domain.beta.kubernetes.io/zone",
                "result": True,
            }


def test_node_label_present__already_set():
    node_data = make_node()
    labels = node_data["metadata"]["labels"]
    with mock_func("node_labels", return_value=labels):
        with mock_func("node_add_label", return_value=node_data):
            actual = kubernetes.node_label_present(
                name="failure-domain.beta.kubernetes.io/region",
                node="minikube",
                value="us-west-1",
            )
            assert actual == {
                "changes": {},
                "result": True,
                "name": "failure-domain.beta.kubernetes.io/region",
                "comment": ("The label is already set and has the specified value"),
            }


def test_node_label_present__update_test_true():
    node_data = make_node()
    labels = node_data["metadata"]["labels"]
    with mock_func("node_labels", return_value=labels):
        with mock_func("node_add_label", return_value=node_data, test=True):
            actual = kubernetes.node_label_present(
                name="failure-domain.beta.kubernetes.io/region",
                node="minikube",
                value="us-east-1",
            )
            assert actual == {
                "changes": {},
                "result": None,
                "name": "failure-domain.beta.kubernetes.io/region",
                "comment": "The label is going to be updated",
            }


def test_node_label_present__update():
    node_data = make_node()
    # Remove some of the defaults to make it simpler
    node_data["metadata"]["labels"] = {
        "failure-domain.beta.kubernetes.io/region": "us-west-1",
    }
    labels = node_data["metadata"]["labels"]
    with mock_func("node_labels", return_value=labels):
        with mock_func("node_add_label", return_value=node_data):
            actual = kubernetes.node_label_present(
                name="failure-domain.beta.kubernetes.io/region",
                node="minikube",
                value="us-east-1",
            )
            assert actual == {
                "changes": {
                    "minikube.failure-domain.beta.kubernetes.io/region": {
                        "new": {"failure-domain.beta.kubernetes.io/region": ("us-east-1")},
                        "old": {"failure-domain.beta.kubernetes.io/region": ("us-west-1")},
                    }
                },
                "result": True,
                "name": "failure-domain.beta.kubernetes.io/region",
                "comment": "The label is already set, changing the value",
            }


def test_node_label_absent__noop_test_true():
    labels = make_node_labels()
    with mock_func("node_labels", return_value=labels, test=True):
        actual = kubernetes.node_label_absent(
            name="non-existent-label",
            node="minikube",
        )
        assert actual == {
            "changes": {},
            "result": None,
            "name": "non-existent-label",
            "comment": "The label does not exist",
        }


def test_node_label_absent__noop():
    labels = make_node_labels()
    with mock_func("node_labels", return_value=labels):
        actual = kubernetes.node_label_absent(
            name="non-existent-label",
            node="minikube",
        )
        assert actual == {
            "changes": {},
            "result": True,
            "name": "non-existent-label",
            "comment": "The label does not exist",
        }


def test_node_label_absent__delete_test_true():
    labels = make_node_labels()
    with mock_func("node_labels", return_value=labels, test=True):
        actual = kubernetes.node_label_absent(
            name="failure-domain.beta.kubernetes.io/region",
            node="minikube",
        )
        assert actual == {
            "changes": {},
            "result": None,
            "name": "failure-domain.beta.kubernetes.io/region",
            "comment": "The label is going to be deleted",
        }


def test_node_label_absent__delete():
    node_data = make_node()
    labels = node_data["metadata"]["labels"].copy()

    node_data["metadata"]["labels"].pop("failure-domain.beta.kubernetes.io/region")

    with mock_func("node_labels", return_value=labels):
        with mock_func("node_remove_label", return_value=node_data):
            actual = kubernetes.node_label_absent(
                name="failure-domain.beta.kubernetes.io/region",
                node="minikube",
            )
            assert actual == {
                "result": True,
                "changes": {
                    "kubernetes.node_label": {
                        "new": "absent",
                        "old": "present",
                    }
                },
                "comment": "Label removed from node",
                "name": "failure-domain.beta.kubernetes.io/region",
            }


def test_namespace_present__create_test_true():
    with mock_func("show_namespace", return_value=None, test=True):
        actual = kubernetes.namespace_present(name="saltstack")
        assert actual == {
            "changes": {},
            "result": None,
            "name": "saltstack",
            "comment": "The namespace is going to be created",
        }


def test_namespace_present__create():
    namespace_data = make_namespace(name="saltstack")
    with mock_func("show_namespace", return_value=None):
        with mock_func("create_namespace", return_value=namespace_data):
            actual = kubernetes.namespace_present(name="saltstack")
            assert actual == {
                "changes": {"namespace": {"new": namespace_data, "old": {}}},
                "result": True,
                "name": "saltstack",
                "comment": "",
            }


def test_namespace_present__noop_test_true():
    namespace_data = make_namespace(name="saltstack")
    with mock_func("show_namespace", return_value=namespace_data, test=True):
        actual = kubernetes.namespace_present(name="saltstack")
        assert actual == {
            "changes": {},
            "result": None,
            "name": "saltstack",
            "comment": "The namespace already exists",
        }


def test_namespace_present__noop():
    namespace_data = make_namespace(name="saltstack")
    with mock_func("show_namespace", return_value=namespace_data):
        actual = kubernetes.namespace_present(name="saltstack")
        assert actual == {
            "changes": {},
            "result": True,
            "name": "saltstack",
            "comment": "The namespace already exists",
        }


def test_namespace_absent__noop_test_true():
    with mock_func("show_namespace", return_value=None, test=True):
        actual = kubernetes.namespace_absent(name="salt")
        assert actual == {
            "changes": {},
            "result": None,
            "name": "salt",
            "comment": "The namespace does not exist",
        }


def test_namespace_absent__noop():
    with mock_func("show_namespace", return_value=None):
        actual = kubernetes.namespace_absent(name="salt")
        assert actual == {
            "changes": {},
            "result": True,
            "name": "salt",
            "comment": "The namespace does not exist",
        }


def test_namespace_absent__delete_test_true():
    namespace_data = make_namespace(name="salt")
    with mock_func("show_namespace", return_value=namespace_data, test=True):
        actual = kubernetes.namespace_absent(name="salt")
        assert actual == {
            "changes": {},
            "result": None,
            "name": "salt",
            "comment": "The namespace is going to be deleted",
        }


def test_namespace_absent__delete_code_200():
    namespace_data = make_namespace(name="salt")
    deleted = namespace_data.copy()
    deleted["code"] = 200
    deleted.update({"code": 200, "message": None})
    with mock_func("show_namespace", return_value=namespace_data):
        with mock_func("delete_namespace", return_value=deleted):
            actual = kubernetes.namespace_absent(name="salt")
            assert actual == {
                "changes": {"kubernetes.namespace": {"new": "absent", "old": "present"}},
                "result": True,
                "name": "salt",
                "comment": "Terminating",
            }


def test_namespace_absent__delete_status_terminating():
    namespace_data = make_namespace(name="salt")
    deleted = namespace_data.copy()
    deleted.update(
        {
            "code": None,
            "status": "Terminating namespace",
            "message": "Terminating this shizzzle yo",
        }
    )
    with mock_func("show_namespace", return_value=namespace_data):
        with mock_func("delete_namespace", return_value=deleted):
            actual = kubernetes.namespace_absent(name="salt")
            assert actual == {
                "changes": {"kubernetes.namespace": {"new": "absent", "old": "present"}},
                "result": True,
                "name": "salt",
                "comment": "Terminating this shizzzle yo",
            }


def test_namespace_absent__delete_status_phase_terminating():
    # This is what kubernetes 1.8.0 looks like when deleting namespaces
    namespace_data = make_namespace(name="salt")
    deleted = namespace_data.copy()
    deleted.update({"code": None, "message": None, "status": {"phase": "Terminating"}})
    with mock_func("show_namespace", return_value=namespace_data):
        with mock_func("delete_namespace", return_value=deleted):
            actual = kubernetes.namespace_absent(name="salt")
            assert actual == {
                "changes": {"kubernetes.namespace": {"new": "absent", "old": "present"}},
                "result": True,
                "name": "salt",
                "comment": "Terminating",
            }


def test_namespace_absent__delete_error():
    namespace_data = make_namespace(name="salt")
    deleted = namespace_data.copy()
    deleted.update({"code": 418, "message": "I' a teapot!", "status": None})
    with mock_func("show_namespace", return_value=namespace_data):
        with mock_func("delete_namespace", return_value=deleted):
            actual = kubernetes.namespace_absent(name="salt")
            assert actual == {
                "changes": {},
                "result": False,
                "name": "salt",
                "comment": f"Something went wrong, response: {deleted}",
            }


def test_deployment_present_with_context():
    """
    Test deployment_present with template context
    """
    context = {"name": "test-deploy", "replicas": 3, "image": "nginx:latest"}
    expected = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "test-deploy"},
        "spec": {"replicas": 3, "template": {"spec": {"containers": [{"image": "nginx:latest"}]}}},
    }

    with mock_func("show_deployment", return_value=None):
        with mock_func("create_deployment", return_value=expected):
            ret = kubernetes.deployment_present(
                name="test-deploy",
                source="salt://k8s/deployment.yaml",
                template="jinja",
                context=context,
            )
            assert ret["result"] is True
            kubernetes.__salt__["kubernetes.create_deployment"].assert_called_with(
                name="test-deploy",
                namespace="default",
                metadata={},
                spec={},
                source="salt://k8s/deployment.yaml",
                template="jinja",
                saltenv="base",
                context=context,
            )


def test_deployment_present_with_metadata_validation():
    """
    Test deployment_present metadata validation
    """
    metadata = {"labels": {"app": "nginx"}, "annotations": {"description": "test deployment"}}
    spec = {"replicas": 3}

    with mock_func("show_deployment", return_value=None):
        with mock_func("create_deployment", return_value={}):
            ret = kubernetes.deployment_present(name="test-deploy", metadata=metadata, spec=spec)
            assert ret["result"] is True
            kubernetes.__salt__["kubernetes.create_deployment"].assert_called_with(
                name="test-deploy",
                namespace="default",
                metadata=metadata,
                spec=spec,
                source="",
                template="",
                saltenv="base",
                context=None,
            )


def test_service_present_with_context():
    """
    Test service_present with template context
    """
    context = {"name": "test-svc", "port": 80, "target_port": 8080}
    expected = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "test-svc"},
        "spec": {"ports": [{"port": 80, "targetPort": 8080}]},
    }

    with mock_func("show_service", return_value=None):
        with mock_func("create_service", return_value=expected):
            ret = kubernetes.service_present(
                name="test-svc", source="salt://k8s/service.yaml", template="jinja", context=context
            )
            assert ret["result"] is True
            kubernetes.__salt__["kubernetes.create_service"].assert_called_with(
                name="test-svc",
                namespace="default",
                metadata={},
                spec={},
                source="salt://k8s/service.yaml",
                template="jinja",
                saltenv="base",
                context=context,
            )


def test_service_present_with_spec_validation():
    """
    Test service_present spec validation
    """
    spec = {
        "ports": [{"port": 80, "targetPort": 8080}],
        "selector": {"app": "nginx"},
        "type": "ClusterIP",
    }

    with mock_func("show_service", return_value=None):
        with mock_func("create_service", return_value={}):
            ret = kubernetes.service_present(name="test-svc", spec=spec)
            assert ret["result"] is True
            kubernetes.__salt__["kubernetes.create_service"].assert_called_with(
                name="test-svc",
                namespace="default",
                metadata={},
                spec=spec,
                source="",
                template="",
                saltenv="base",
                context=None,
            )


def test_configmap_present_with_context():
    """
    Test configmap_present with template context
    """
    context = {"config_value": "some configuration"}
    expected = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "data": {"config.txt": "some configuration"},
    }

    with mock_func("show_configmap", return_value=None):
        with mock_func("create_configmap", return_value=expected):
            ret = kubernetes.configmap_present(
                name="test-cm",
                source="salt://k8s/configmap.yaml",
                template="jinja",
                context=context,
            )
            assert ret["result"] is True
            kubernetes.__salt__["kubernetes.create_configmap"].assert_called_with(
                name="test-cm",
                namespace="default",
                data={},
                source="salt://k8s/configmap.yaml",
                template="jinja",
                saltenv="base",
                context=context,
            )


def test_secret_present_with_context():
    """
    Test secret_present with template context
    """
    context = {"password": "supersecret"}
    expected = {
        "apiVersion": "v1",
        "kind": "Secret",
        "data": {"password": "c3VwZXJzZWNyZXQ="},  # base64 encoded
    }

    with mock_func("show_secret", return_value=None):
        with mock_func("create_secret", return_value=expected):
            ret = kubernetes.secret_present(
                name="test-secret",
                source="salt://k8s/secret.yaml",
                template="jinja",
                context=context,
            )
            assert ret["result"] is True
            kubernetes.__salt__["kubernetes.create_secret"].assert_called_with(
                name="test-secret",
                namespace="default",
                data={},
                source="salt://k8s/secret.yaml",
                template="jinja",
                saltenv="base",
                context=context,
                type=None,
                metadata={},
            )


def test_replicaset_absent__noop_test_true():
    """Test replicaset_absent with test=True and replicaset does not exist"""
    with mock_func("show_replicaset", return_value=None, test=True):
        actual = kubernetes.replicaset_absent(name="test-rs")
        assert actual == {
            "comment": "The replicaset does not exist",
            "changes": {},
            "name": "test-rs",
            "result": None,
        }


def test_replicaset_absent__noop():
    """Test replicaset_absent when replicaset does not exist"""
    with mock_func("show_replicaset", return_value=None):
        actual = kubernetes.replicaset_absent(name="test-rs")
        assert actual == {
            "comment": "The replicaset does not exist",
            "changes": {},
            "name": "test-rs",
            "result": True,
        }


def test_replicaset_absent__delete_test_true():
    """Test replicaset_absent with test=True and replicaset exists"""
    with mock_func("show_replicaset", return_value={"metadata": {"name": "test-rs"}}, test=True):
        actual = kubernetes.replicaset_absent(name="test-rs")
        assert actual == {
            "comment": "The replicaset is going to be deleted",
            "changes": {},
            "name": "test-rs",
            "result": None,
        }


def test_replicaset_absent__delete():
    """Test successful replicaset deletion"""
    with mock_func("show_replicaset", return_value={"metadata": {"name": "test-rs"}}):
        with mock_func("delete_replicaset", return_value={"code": 200, "message": "Deleted"}):
            actual = kubernetes.replicaset_absent(name="test-rs")
            assert actual == {
                "comment": "Deleted",
                "changes": {"kubernetes.replicaset": {"new": "absent", "old": "present"}},
                "name": "test-rs",
                "result": True,
            }


def test_replicaset_present__create_test_true():
    """Test replicaset_present with test=True for creation"""
    with mock_func("show_replicaset", return_value=None, test=True):
        actual = kubernetes.replicaset_present(
            name="test-rs",
            metadata={"labels": {"app": "nginx"}},
            spec={"replicas": 3},
        )
        assert actual == {
            "comment": "The replicaset is going to be created",
            "changes": {},
            "name": "test-rs",
            "result": None,
        }


def test_replicaset_present__create():
    """Test successful replicaset creation"""
    with mock_func("show_replicaset", return_value=None):
        with mock_func(
            "create_replicaset",
            return_value={"metadata": {"name": "test-rs"}, "spec": {"replicas": 3}},
        ):
            actual = kubernetes.replicaset_present(
                name="test-rs",
                metadata={"labels": {"app": "nginx"}},
                spec={"replicas": 3},
            )
            assert actual == {
                "comment": "",
                "changes": {
                    "metadata": {"labels": {"app": "nginx"}},
                    "spec": {"replicas": 3},
                },
                "name": "test-rs",
                "result": True,
            }


def test_replicaset_present__create_with_template():
    """Test replicaset creation using template with context"""
    context = {"name": "test-rs", "replicas": 3, "image": "nginx:latest"}

    with mock_func("show_replicaset", return_value=None):
        with mock_func("create_replicaset", return_value={}):
            ret = kubernetes.replicaset_present(
                name="test-rs",
                source="salt://k8s/replicaset.yaml",
                template="jinja",
                context=context,
            )
            assert ret["result"] is True
            kubernetes.__salt__["kubernetes.create_replicaset"].assert_called_with(
                name="test-rs",
                namespace="default",
                metadata={},
                spec={},
                source="salt://k8s/replicaset.yaml",
                template="jinja",
                saltenv="base",
                context=context,
            )


def test_persistent_volume_absent__noop_test_true():
    """Test persistent_volume_absent with test=True and volume does not exist"""
    with mock_func("show_persistent_volume", return_value=None, test=True):
        actual = kubernetes.persistent_volume_absent(name="test-pv")
        assert actual == {
            "comment": "The persistent volume does not exist",
            "changes": {},
            "name": "test-pv",
            "result": None,
        }


def test_persistent_volume_absent__noop():
    """Test persistent_volume_absent when volume does not exist"""
    with mock_func("show_persistent_volume", return_value=None):
        actual = kubernetes.persistent_volume_absent(name="test-pv")
        assert actual == {
            "comment": "The persistent volume does not exist",
            "changes": {},
            "name": "test-pv",
            "result": True,
        }


def test_persistent_volume_absent__delete_test_true():
    """Test persistent_volume_absent with test=True and volume exists"""
    with mock_func(
        "show_persistent_volume", return_value={"metadata": {"name": "test-pv"}}, test=True
    ):
        actual = kubernetes.persistent_volume_absent(name="test-pv")
        assert actual == {
            "comment": "The persistent volume is going to be deleted",
            "changes": {},
            "name": "test-pv",
            "result": None,
        }


def test_persistent_volume_absent__delete():
    """Test successful persistent volume deletion"""
    with mock_func("show_persistent_volume", return_value={"metadata": {"name": "test-pv"}}):
        with mock_func("delete_persistent_volume", return_value={"message": "Deleted"}):
            actual = kubernetes.persistent_volume_absent(name="test-pv")
            assert actual == {
                "comment": "Deleted",
                "changes": {"kubernetes.persistent_volume": {"new": "absent", "old": "present"}},
                "name": "test-pv",
                "result": True,
            }


def test_persistent_volume_present__create_test_true():
    """Test persistent_volume_present with test=True for creation"""
    with mock_func("show_persistent_volume", return_value=None, test=True):
        actual = kubernetes.persistent_volume_present(
            name="test-pv",
            spec={
                "capacity": {"storage": "1Gi"},
                "access_modes": ["ReadWriteOnce"],
                "nfs": {"path": "/share", "server": "nfs.example.com"},
            },
        )
        assert actual == {
            "comment": "The persistent volume is going to be created",
            "changes": {},
            "name": "test-pv",
            "result": None,
        }


def test_persistent_volume_present__create():
    """Test successful persistent volume creation"""
    spec = {
        "capacity": {"storage": "1Gi"},
        "access_modes": ["ReadWriteOnce"],
        "nfs": {"path": "/share", "server": "nfs.example.com"},
    }

    with mock_func("show_persistent_volume", return_value=None):
        with mock_func(
            "create_persistent_volume",
            return_value={"metadata": {"name": "test-pv"}, "spec": spec},
        ):
            actual = kubernetes.persistent_volume_present(name="test-pv", spec=spec)
            assert actual == {
                "comment": "",
                "changes": {
                    "test-pv": {
                        "new": {"metadata": {"name": "test-pv"}, "spec": spec},
                        "old": {},
                    }
                },
                "name": "test-pv",
                "result": True,
            }


def test_persistent_volume_present__create_with_source():
    """Test persistent volume creation using source file"""
    mock_pv_yaml = """
apiVersion: v1
kind: PersistentVolume
metadata:
  name: test-pv
spec:
  capacity:
    storage: 1Gi
  access_modes:  # Using snake_case in source yaml
    - ReadWriteOnce
  persistent_volume_reclaim_policy: Retain
  nfs:
    path: /share
    server: nfs.example.com
"""
    mock_pv_dict = {
        "kind": "PersistentVolume",
        "metadata": {"name": "test-pv"},
        "spec": {
            "capacity": {"storage": "1Gi"},
            "access_modes": ["ReadWriteOnce"],
            "nfs": {"path": "/share", "server": "nfs.example.com"},
        },
    }

    spec = {
        "capacity": {"storage": "1Gi"},
        "access_modes": ["ReadWriteOnce"],
        "nfs": {"path": "/share", "server": "nfs.example.com"},
    }

    with mock_func("show_persistent_volume", return_value=None):
        with (
            patch("salt.utils.files.fopen", mock_open(read_data=mock_pv_yaml)),
            patch("salt.utils.yaml.safe_load", return_value=mock_pv_dict),
        ):
            with mock_func("create_persistent_volume", return_value=mock_pv_dict):
                actual = kubernetes.persistent_volume_present(
                    name="test-pv", spec=spec, source="/mock/pv.yaml"
                )
                assert actual == {
                    "comment": "",
                    "changes": {"test-pv": {"old": {}, "new": mock_pv_dict}},
                    "name": "test-pv",
                    "result": True,
                }
