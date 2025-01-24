import logging
import subprocess
import time

import pytest
from pytest_kind import KindCluster
from saltfactories.utils import random_string

from saltext.kubernetes import PACKAGE_ROOT

# Reset the root logger to its default level(because salt changed it)
logging.root.setLevel(logging.WARNING)

log = logging.getLogger(__name__)

# Supported Kubernetes versions for testing based on v0.25.0 of kind - kind v0.26.0 is latest
K8S_VERSIONS = [
    "v1.26.15",
    "v1.27.16",
    "v1.28.15",
    "v1.29.10",
    "v1.30.6",
    "v1.31.2",
]

# This swallows all logging to stdout.
# To show select logs, set --log-cli-level=<level>
for handler in logging.root.handlers[:]:  # pragma: no cover
    logging.root.removeHandler(handler)
    handler.close()


@pytest.fixture(scope="package")
def pillar_tree(tmp_path_factory):
    """
    Create a pillar tree in a temporary directory.
    """
    pillar_tree = tmp_path_factory.mktemp("pillar")
    top_file = pillar_tree / "top.sls"
    kubernetes_file = pillar_tree / "kubernetes.sls"

    # Create default top file
    top_file.write_text(
        """
base:
  '*':
    - kubernetes
"""
    )

    # Create empty kubernetes pillar file
    kubernetes_file.write_text("")

    return pillar_tree


@pytest.fixture(scope="package")
def master_config(pillar_tree):
    """Salt master configuration overrides for integration tests."""
    return {
        "pillar_roots": {
            "base": [str(pillar_tree)],
        },
        "open_mode": True,
        "timeout": 120,
    }


@pytest.fixture(scope="package")
def master(salt_factories, master_config):  # pragma: no cover
    return salt_factories.salt_master_daemon(random_string("master-"), overrides=master_config)


@pytest.fixture(scope="package")
def minion_config(kind_cluster):
    """Salt minion configuration overrides for integration tests."""
    return {
        "kubernetes.kubeconfig": str(kind_cluster.kubeconfig_path),
        "kubernetes.context": "kind-salt-test",
        "file_roots": {
            "base": [str(PACKAGE_ROOT)],
        },
        "providers": {
            "pkg": "kubernetes",
        },
    }


@pytest.fixture(scope="package")
def minion(master, minion_config):  # pragma: no cover
    return master.salt_minion_daemon(random_string("minion-"), overrides=minion_config)


@pytest.fixture(scope="session", params=K8S_VERSIONS)
def kind_cluster(request):  # pylint: disable=too-many-statements
    """Create Kind cluster for testing with specified Kubernetes version"""
    cluster = KindCluster(name="salt-test", image=f"kindest/node:{request.param}")
    try:
        cluster.create()

        # Initial wait for cluster to start
        time.sleep(10)  # Increased initial wait

        # Wait for and validate cluster readiness using kubectl
        retries = 12  # Increased retries
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
                time.sleep(10)  # Increased sleep between retries

        yield cluster
    finally:
        try:
            cluster.delete()
        except Exception:  # pylint: disable=broad-except
            log.error("Failed to delete cluster", exc_info=True)
