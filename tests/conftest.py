import logging
import os
import subprocess

import pytest
from pytest_kind import KindCluster
from saltfactories.utils import random_string

from saltext.kubernetes import PACKAGE_ROOT

# Reset the root logger to its default level(because salt changed it)
logging.root.setLevel(logging.WARNING)

log = logging.getLogger(__name__)

# Supported Kubernetes versions for testing based on
# standard support versions across kubernetes deployments
# Some clouds may have different supported versions
K8S_VERSIONS = [
    "v1.28.15",
    "v1.32.0",
]  # pragma: no cover

# This swallows all logging to stdout.
# To show select logs, set --log-cli-level=<level>
for handler in logging.root.handlers[:]:  # pragma: no cover
    logging.root.removeHandler(handler)
    handler.close()


@pytest.fixture(scope="session")
def salt_factories_config():  # pragma: no cover
    """
    Return a dictionary with the keyword arguments for FactoriesManager
    """
    return {
        "code_dir": str(PACKAGE_ROOT),
        "inject_sitecustomize": "COVERAGE_PROCESS_START" in os.environ,
        "start_timeout": 120 if os.environ.get("CI") else 60,
    }


@pytest.fixture(scope="module")
def master_config():  # pragma: no cover
    """
    Default master configuration for kubernetes tests
    """
    return {
        "open_mode": True,
        "timeout": 120,
    }


@pytest.fixture(scope="module")
def master_config_overrides():  # pragma: no cover
    """
    Override the default configuration per package
    """
    return {}


@pytest.fixture(scope="module")
def master(salt_factories, master_config, master_config_overrides):  # pragma: no cover
    return salt_factories.salt_master_daemon(
        random_string("master-"), defaults=master_config, overrides=master_config_overrides
    )


@pytest.fixture(scope="module")
def minion_config(kind_cluster):  # pragma: no cover
    """
    Default minion configuration for kubernetes tests
    """
    return {
        "kubernetes.kubeconfig": str(kind_cluster.kubeconfig_path),
        "kubernetes.context": "kind-salt-test",
        "providers": {"pkg": "kubernetes"},
        "open_mode": True,
    }


@pytest.fixture(scope="module")
def minion_config_overrides():  # pragma: no cover
    """
    Override the default configuration per package
    """
    return {}


@pytest.fixture(scope="module")
def minion(master, minion_config, minion_config_overrides):  # pragma: no cover
    return master.salt_minion_daemon(
        random_string("minion-"), defaults=minion_config, overrides=minion_config_overrides
    )


@pytest.fixture(scope="session", params=K8S_VERSIONS)
def kind_cluster(request):  # pragma: no cover
    """
    Create Kind cluster for testing with specified Kubernetes version
    """
    cluster = KindCluster(name="salt-test", image=f"kindest/node:{request.param}")
    try:
        cluster.create()

        # Wait for and validate cluster readiness using kubectl
        retries = 5
        context = "kind-salt-test"
        while retries > 0:
            try:
                # Verify cluster is accessible
                kubectl_cmd = [
                    "kubectl",
                    "--context",
                    context,
                    "--kubeconfig",
                    str(cluster.kubeconfig_path),
                ]

                subprocess.run(
                    kubectl_cmd + ["cluster-info"],
                    check=True,
                    capture_output=True,
                    text=True,
                )

                # Wait longer for node readiness
                subprocess.run(
                    kubectl_cmd
                    + ["wait", "--for=condition=ready", "nodes", "--all", "--timeout=120s"],
                    check=True,
                    capture_output=True,
                    text=True,
                )

                # Verify core services are running with longer timeout
                subprocess.run(
                    kubectl_cmd
                    + [
                        "wait",
                        "--for=condition=Ready",
                        "pods",
                        "--all",
                        "-n",
                        "kube-system",
                        "--timeout=120s",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                break
            except subprocess.CalledProcessError as exc:  # pylint: disable=try-except-raise
                retries -= 1
                if retries == 0:
                    log.error("Failed to validate cluster:")
                    log.error("stdout: %s", exc.stdout)
                    log.error("stderr: %s", exc.stderr)
                    raise

        yield cluster
    finally:
        try:
            cluster.delete()
        except Exception:  # pylint: disable=broad-except
            log.error("Failed to delete cluster", exc_info=True)
