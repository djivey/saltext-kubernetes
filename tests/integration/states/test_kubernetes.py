import logging
import sys
import time

import pytest

log = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Only run on Linux platforms")


@pytest.fixture(scope="module")
def state_tree(base_env_state_tree_root_dir):
    return base_env_state_tree_root_dir


@pytest.fixture(scope="module")
def kubernetes_master_config(master_config_defaults, state_tree):
    """Kubernetes specific master config"""
    config = master_config_defaults.copy()
    config["file_roots"] = {"base": [str(state_tree)]}
    return config


@pytest.fixture(scope="module")
def kubernetes_salt_master(salt_factories, kubernetes_master_config):
    factory = salt_factories.salt_master_daemon("kube-master", defaults=kubernetes_master_config)
    with factory.started():
        yield factory


@pytest.fixture(scope="module")
def kubernetes_salt_minion(kubernetes_salt_master, minion_config_defaults):
    assert kubernetes_salt_master.is_running()
    factory = kubernetes_salt_master.salt_minion_daemon(
        "kube-minion",
        defaults=minion_config_defaults,
    )
    with factory.started():
        salt_call_cli = factory.salt_call_cli()
        ret = salt_call_cli.run("saltutil.sync_all", _timeout=120)
        assert ret.returncode == 0, ret
        yield factory


@pytest.fixture(scope="module")
def salt_call_cli(kubernetes_salt_minion):
    return kubernetes_salt_minion.salt_call_cli()


class TestKubernetesState:
    """Test kubernetes state module functionality"""

    def test_namespace_present_absent(self, salt_call_cli, state_tree):
        """Test namespace creation and deletion via states"""
        test_ns = "test-namespace-state"

        # Create namespace state file
        state_file = state_tree / "test_namespace.sls"
        state_file.write_text(
            f"""
test_namespace:
  kubernetes.namespace_present:
    - name: {test_ns}
    - labels:
        app: test
        environment: testing
"""
        )

        try:
            # Apply namespace present state
            ret = salt_call_cli.run("state.apply", "test_namespace")
            assert ret.returncode == 0
            assert ret.data
            # Check state result
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]
            assert test_ns in state_ret["changes"].get("namespace", {}).get("new", {}).get(
                "metadata", {}
            ).get("name", "")

            # Verify namespace exists
            ret = salt_call_cli.run("kubernetes.show_namespace", name=test_ns)
            assert ret.returncode == 0
            assert ret.data["metadata"]["name"] == test_ns
            assert ret.data["status"]["phase"] == "Active"

            # Test idempotency
            ret = salt_call_cli.run("state.apply", "test_namespace")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert not state_ret["changes"]
            assert "already exists" in state_ret["comment"]

        finally:
            # Test namespace removal
            state_file.write_text(
                f"""
remove_test_namespace:
  kubernetes.namespace_absent:
    - name: {test_ns}
"""
            )

            # Apply namespace absent state
            ret = salt_call_cli.run("state.apply", "test_namespace")
            assert ret.returncode == 0
            # ...rest of cleanup assertions...

    def test_pod_present_absent(self, salt_call_cli, state_tree):
        """Test pod creation and deletion via states"""
        test_pod = "test-pod-state"

        # Create pod state file
        state_file = state_tree / "test_pod.sls"
        state_file.write_text(
            f"""
test_pod:
  kubernetes.pod_present:
    - name: {test_pod}
    - namespace: default
    - metadata:
        labels:
          app: test
    - spec:
        containers:
          - name: nginx
            image: nginx:latest
            ports:
              - containerPort: 80
"""
        )

        try:
            # Apply pod present state
            ret = salt_call_cli.run("state.apply", "test_pod")
            assert ret.returncode == 0
            assert ret.data
            # Check state result
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]

            # Verify pod creation changes - check metadata/spec was applied correctly
            assert state_ret["changes"]["metadata"]["labels"] == {
                "app": "test"
            }  # Fixed: "test" instead of test
            assert state_ret["changes"]["spec"]["containers"][0]["name"] == "nginx"

            # Add wait for pod creation
            time.sleep(10)

            # Verify pod exists and is running using module
            ret = salt_call_cli.run("kubernetes.show_pod", name=test_pod, namespace="default")
            assert ret.returncode == 0
            assert ret.data["metadata"]["name"] == test_pod

            # Test idempotency - running again should result in expected failure
            ret = salt_call_cli.run("state.apply", "test_pod")
            # We expect returncode=1 since pods cannot be replaced
            assert isinstance(ret.data, dict)
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is False
            assert not state_ret["changes"]  # No changes should be made
            assert "salt is currently unable to replace a pod" in state_ret["comment"]

        finally:
            # Test pod removal
            state_file.write_text(
                f"""
remove_test_pod:
  kubernetes.pod_absent:
    - name: {test_pod}
    - namespace: default
"""
            )

            # Apply pod absent state
            ret = salt_call_cli.run("state.apply", "test_pod")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]

            # Verify pod is gone (with retry since deletion can take time)
            max_retries = 10
            for _ in range(max_retries):
                ret = salt_call_cli.run("kubernetes.show_pod", name=test_pod, namespace="default")
                if ret.data is None:
                    break
                time.sleep(5)
            else:
                pytest.fail("Pod was not deleted within expected time")

    def test_deployment_present_absent(self, salt_call_cli, state_tree):
        """Test deployment creation and deletion via states"""
        test_deployment = "test-deployment-state"

        # Create deployment state file
        state_file = state_tree / "test_deployment.sls"
        state_file.write_text(
            f"""
test_deployment:
  kubernetes.deployment_present:
    - name: {test_deployment}
    - namespace: default
    - metadata:
        labels:
          app: test
    - spec:
        replicas: 2
        selector:
          matchLabels:
            app: test
        template:
          metadata:
            labels:
              app: test
          spec:
            containers:
              - name: nginx
                image: nginx:latest
                ports:
                  - containerPort: 80
"""
        )

        try:
            # Apply deployment present state
            ret = salt_call_cli.run("state.apply", "test_deployment")
            assert ret.returncode == 0
            assert ret.data
            # Check state result
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]
            assert "metadata" in state_ret["changes"]
            assert "spec" in state_ret["changes"]
            assert state_ret["changes"]["metadata"]["labels"] == {"app": "test"}
            assert state_ret["changes"]["spec"]["replicas"] == 2

            # Verify deployment exists
            ret = salt_call_cli.run(
                "kubernetes.show_deployment", name=test_deployment, namespace="default"
            )
            assert ret.returncode == 0
            assert ret.data["metadata"]["name"] == test_deployment
            assert ret.data["spec"]["replicas"] == 2

            # Apply same state again - Kubernetes will recreate the deployment
            ret = salt_call_cli.run("state.apply", "test_deployment")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["comment"] == "The deployment is already present. Forcing recreation"
            assert state_ret["changes"]  # Changes will be present due to recreation

        finally:
            # Test deployment removal
            state_file.write_text(
                f"""
remove_deployment:
  kubernetes.deployment_absent:
    - name: {test_deployment}
    - namespace: default
"""
            )

            # Apply deployment absent state
            ret = salt_call_cli.run("state.apply", "test_deployment")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]

            # Verify deployment is gone (with retry since deletion can take time)
            max_retries = 10
            for _ in range(max_retries):
                ret = salt_call_cli.run(
                    "kubernetes.show_deployment", name=test_deployment, namespace="default"
                )
                if ret.data is None:
                    break
                time.sleep(5)
            else:
                pytest.fail("Deployment was not deleted within expected time")

    def test_secret_present_absent(self, salt_call_cli, state_tree):
        """Test secret creation and deletion via states"""
        test_secret = "test-secret-state"

        # Create secret state file
        state_file = state_tree / "test_secret.sls"
        state_file.write_text(
            f"""
test_secret:
  kubernetes.secret_present:
    - name: {test_secret}
    - namespace: default
    - data:
        username: YWRtaW4=  # base64 encoded "admin"
        password: cGFzc3dvcmQ=  # base64 encoded "password"
    - type: Opaque
"""
        )

        try:
            # Apply secret present state
            ret = salt_call_cli.run("state.apply", "test_secret")
            assert ret.returncode == 0
            assert ret.data
            # Check state result
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]
            # Verify secret data (keys only since values are sensitive)
            assert sorted(state_ret["changes"]["data"]) == ["password", "username"]

            # Verify secret exists using module
            ret = salt_call_cli.run(
                "kubernetes.show_secret",
                name=test_secret,
                namespace="default",
                decode=True,
            )
            assert ret.returncode == 0
            assert ret.data["metadata"]["name"] == test_secret
            assert ret.data["data"]["username"] == "admin"
            assert ret.data["data"]["password"] == "password"

            # Test reapplication - secrets are recreated like deployments
            ret = salt_call_cli.run("state.apply", "test_secret")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["comment"] == "The secret is already present. Forcing recreation"
            assert state_ret["changes"]  # Changes will be present due to recreation
            assert sorted(state_ret["changes"]["data"]) == ["password", "username"]

            # Verify secret still exists with same values
            ret = salt_call_cli.run(
                "kubernetes.show_secret",
                name=test_secret,
                namespace="default",
                decode=True,
            )
            assert ret.returncode == 0
            assert ret.data["data"]["username"] == "admin"
            assert ret.data["data"]["password"] == "password"

            # Test updating the secret
            state_file.write_text(
                f"""
test_secret:
  kubernetes.secret_present:
    - name: {test_secret}
    - namespace: default
    - data:
        username: bmV3YWRtaW4=  # base64 encoded "newadmin"
        password: bmV3cGFzcw==  # base64 encoded "newpass"
    - type: Opaque
"""
            )
            ret = salt_call_cli.run("state.apply", "test_secret")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]
            assert sorted(state_ret["changes"]["data"]) == ["password", "username"]

            # Verify updated values
            ret = salt_call_cli.run(
                "kubernetes.show_secret",
                name=test_secret,
                namespace="default",
                decode=True,
            )
            assert ret.returncode == 0
            assert ret.data["data"]["username"] == "newadmin"
            assert ret.data["data"]["password"] == "newpass"

        finally:
            # Test secret removal
            state_file.write_text(
                f"""
remove_secret:
  kubernetes.secret_absent:
    - name: {test_secret}
    - namespace: default
"""
            )

            # Apply secret absent state
            ret = salt_call_cli.run("state.apply", "test_secret")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]

            # Verify secret is gone
            ret = salt_call_cli.run(
                "kubernetes.show_secret",
                name=test_secret,
                namespace="default",
            )
            assert ret.data is None

    def test_service_present_absent(self, salt_call_cli, state_tree):
        """Test service creation and deletion via states"""
        test_service = "test-service-state"

        # Create service state file
        state_file = state_tree / "test_service.sls"
        state_file.write_text(
            f"""
test_service:
  kubernetes.service_present:
    - name: {test_service}
    - namespace: default
    - metadata:
        labels:
          app: test
    - spec:
        ports:
        - port: 80
          targetPort: 8080
          name: http
        - port: 443
          targetPort: 8443
          name: https
        selector:
          app: test
        type: ClusterIP
"""
        )

        try:
            # Apply service present state
            ret = salt_call_cli.run("state.apply", "test_service")
            assert ret.returncode == 0
            assert ret.data
            # Check state result
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]
            assert "metadata" in state_ret["changes"]
            assert "spec" in state_ret["changes"]
            assert state_ret["changes"]["metadata"]["labels"] == {"app": "test"}
            assert len(state_ret["changes"]["spec"]["ports"]) == 2

            # Verify service exists
            ret = salt_call_cli.run(
                "kubernetes.show_service", name=test_service, namespace="default"
            )
            assert ret.returncode == 0
            assert ret.data["metadata"]["name"] == test_service
            assert len(ret.data["spec"]["ports"]) == 2
            assert ret.data["spec"]["type"] == "ClusterIP"

            # Test reapplication
            ret = salt_call_cli.run("state.apply", "test_service")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["comment"] == "The service is already present. Forcing recreation"
            assert state_ret["changes"]

        finally:
            # Test service removal
            state_file.write_text(
                f"""
remove_service:
  kubernetes.service_absent:
    - name: {test_service}
    - namespace: default
"""
            )

            # Apply service absent state
            ret = salt_call_cli.run("state.apply", "test_service")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]

            # Verify service is gone
            ret = salt_call_cli.run(
                "kubernetes.show_service",
                name=test_service,
                namespace="default",
            )
            assert ret.data is None

    def test_configmap_present_absent(self, salt_call_cli, state_tree):
        """Test configmap creation and deletion via states"""
        test_configmap = "test-configmap-state"

        # Create configmap state file
        state_file = state_tree / "test_configmap.sls"
        state_file.write_text(
            f"""
test_configmap:
  kubernetes.configmap_present:
    - name: {test_configmap}
    - namespace: default
    - data:
        config.yaml: |
          foo: bar
          key: value
        app.properties: |
          app.name=myapp
          app.port=8080
"""
        )

        try:
            # Apply configmap present state
            ret = salt_call_cli.run("state.apply", "test_configmap")
            assert ret.returncode == 0
            assert ret.data
            # Check state result
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]
            assert "data" in state_ret["changes"]
            assert sorted(state_ret["changes"]["data"].keys()) == ["app.properties", "config.yaml"]

            # Verify configmap exists
            ret = salt_call_cli.run(
                "kubernetes.show_configmap", name=test_configmap, namespace="default"
            )
            assert ret.returncode == 0
            assert ret.data["metadata"]["name"] == test_configmap
            assert "config.yaml" in ret.data["data"]
            assert "app.properties" in ret.data["data"]

            # Test reapplication
            ret = salt_call_cli.run("state.apply", "test_configmap")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["comment"] == "The configmap is already present. Forcing recreation"
            assert state_ret["changes"]  # Changes will be present due to recreation
            assert sorted(state_ret["changes"]["data"].keys()) == ["app.properties", "config.yaml"]

            # Verify configmap still exists with same data
            ret = salt_call_cli.run(
                "kubernetes.show_configmap", name=test_configmap, namespace="default"
            )
            assert ret.returncode == 0
            assert ret.data["metadata"]["name"] == test_configmap
            assert ret.data["data"]["config.yaml"].strip() == "foo: bar\nkey: value"
            assert ret.data["data"]["app.properties"].strip() == "app.name=myapp\napp.port=8080"

            # Test updating the configmap
            state_file.write_text(
                f"""
test_configmap:
  kubernetes.configmap_present:
    - name: {test_configmap}
    - namespace: default
    - data:
        config.yaml: |
          foo: newbar
          key: newvalue
        app.properties: |
          app.name=newapp
          app.port=9090
"""
            )
            ret = salt_call_cli.run("state.apply", "test_configmap")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]
            assert sorted(state_ret["changes"]["data"].keys()) == ["app.properties", "config.yaml"]

        finally:
            # Test configmap removal
            state_file.write_text(
                f"""
remove_configmap:
  kubernetes.configmap_absent:
    - name: {test_configmap}
    - namespace: default
"""
            )

            # Apply configmap absent state
            ret = salt_call_cli.run("state.apply", "test_configmap")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]

            # Verify configmap is gone
            ret = salt_call_cli.run(
                "kubernetes.show_configmap",
                name=test_configmap,
                namespace="default",
            )
            assert ret.data is None

    def test_replicaset_present_absent(self, salt_call_cli, state_tree, caplog):
        """Test replicaset present and absent states"""
        caplog.set_level(logging.INFO)
        test_rs = "test-replicaset-state"

        # Create replicaset state file
        state_file = state_tree / "test_replicaset.sls"
        state_file.write_text(
            f"""
    test_replicaset:
      kubernetes.replicaset_present:
        - name: {test_rs}
        - namespace: default
        - metadata:
            labels:
              app: test
        - spec:
            replicas: 2
            selector:
              matchLabels:
                app: test
            template:
              metadata:
                labels:
                  app: test
              spec:
                containers:
                  - name: nginx
                    image: nginx:latest
                    ports:
                      - containerPort: 80
    """
        )

        try:
            # Apply replicaset present state
            ret = salt_call_cli.run("state.apply", "test_replicaset")
            assert ret.returncode == 0
            assert ret.data
            # Check state result
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]

            # Verify replicaset exists in k8s
            ret = salt_call_cli.run("kubernetes.show_replicaset", name=test_rs, namespace="default")
            assert ret.returncode == 0
            assert ret.data["metadata"]["name"] == test_rs
            assert ret.data["spec"]["replicas"] == 2

            # Test idempotency
            ret = salt_call_cli.run("state.apply", "test_replicaset")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True

        finally:
            # Test absent state
            state_file.write_text(
                f"""
    remove_replicaset:
      kubernetes.replicaset_absent:
        - name: {test_rs}
        - namespace: default
    """
            )

            ret = salt_call_cli.run("state.apply", "test_replicaset")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]

            # Verify replicaset is gone
            ret = salt_call_cli.run("kubernetes.show_replicaset", name=test_rs, namespace="default")
            assert ret.returncode == 0
            assert ret.data is None

    def test_replicaset_present_with_template(self, salt_call_cli, state_tree, caplog):
        """Test replicaset present state using template"""
        caplog.set_level(logging.INFO)
        test_rs = "test-replicaset-template"

        # Create template file
        template_file = state_tree / "replicaset-template.yaml.jinja"
        template_file.write_text(
            """
    apiVersion: apps/v1
    kind: ReplicaSet
    metadata:
      name: {{ name }}
      namespace: {{ namespace }}
      labels:
        app: {{ app_label }}
    spec:
      replicas: {{ replicas }}
      selector:
        matchLabels:
          app: {{ app_label }}
      template:
        metadata:
          labels:
            app: {{ app_label }}
        spec:
          containers:
          - name: {{ name }}
            image: {{ image }}
            ports:
            - containerPort: 80
    """
        )

        # Create state file using template
        state_file = state_tree / "test_replicaset_template.sls"
        state_file.write_text(
            f"""
    test_replicaset:
      kubernetes.replicaset_present:
        - name: {test_rs}
        - namespace: default
        - source: salt://replicaset-template.yaml.jinja
        - template: jinja
        - context:
            name: {test_rs}
            namespace: default
            app_label: test
            replicas: 2
            image: nginx:latest
    """
        )

        try:
            # Apply state
            ret = salt_call_cli.run("state.apply", "test_replicaset_template")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]

            # Verify replicaset was created correctly
            ret = salt_call_cli.run("kubernetes.show_replicaset", name=test_rs, namespace="default")
            assert ret.returncode == 0
            assert ret.data["metadata"]["name"] == test_rs
            assert ret.data["spec"]["replicas"] == 2
            assert ret.data["spec"]["template"]["spec"]["containers"][0]["image"] == "nginx:latest"

        finally:
            # Cleanup
            salt_call_cli.run(
                "state.single",
                "kubernetes.replicaset_absent",
                name=test_rs,
                namespace="default",
            )

    def test_replicaset_present_test_mode(self, salt_call_cli, state_tree, caplog):
        """Test replicaset present state in test mode"""
        caplog.set_level(logging.INFO)
        test_rs = "test-replicaset-testmode"

        # Create state file
        state_file = state_tree / "test_replicaset.sls"
        state_file.write_text(
            f"""
    test_replicaset:
      kubernetes.replicaset_present:
        - name: {test_rs}
        - namespace: default
        - metadata:
            labels:
              app: test
        - spec:
            replicas: 2
            selector:
              matchLabels:
                app: test
            template:
              metadata:
                labels:
                  app: test
              spec:
                containers:
                  - name: nginx
                    image: nginx:latest
    """
        )

        # Test with test=True
        ret = salt_call_cli.run("state.apply", "test_replicaset", test=True)
        assert ret.returncode == 0
        state_ret = ret.data[next(iter(ret.data))]
        assert state_ret["result"] is None
        assert state_ret["comment"] == "The replicaset is going to be created"
        assert not state_ret["changes"]

        # Create replicaset
        ret = salt_call_cli.run("state.apply", "test_replicaset")
        assert ret.returncode == 0

        try:
            # Update state file with new replicas
            state_file.write_text(
                f"""
    test_replicaset:
      kubernetes.replicaset_present:
        - name: {test_rs}
        - namespace: default
        - metadata:
            labels:
              app: test
        - spec:
            replicas: 3
            selector:
              matchLabels:
                app: test
            template:
              metadata:
                labels:
                  app: test
              spec:
                containers:
                  - name: nginx
                    image: nginx:latest
    """
            )

            # Test update with test=True
            ret = salt_call_cli.run("state.apply", "test_replicaset", test=True)
            assert ret.returncode == 0
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is None
            assert "going to be replaced" in state_ret["comment"]
            assert not state_ret["changes"]

        finally:
            # Cleanup
            salt_call_cli.run(
                "state.single",
                "kubernetes.replicaset_absent",
                name=test_rs,
                namespace="default",
            )

    def test_persistent_volume_present_absent(self, salt_call_cli, state_tree):
        """Test persistent_volume_present and persistent_volume_absent states"""
        test_pv = "salt-test-pv-state"

        # Create state file for PV present
        state_file = state_tree / "test_persistent_volume.sls"
        state_file.write_text(
            f"""
test_persistent_volume:
  kubernetes.persistent_volume_present:
    - name: {test_pv}
    - spec:
        capacity:
          storage: 1Gi
        access_modes:
          - ReadWriteOnce
        persistent_volume_reclaim_policy: Retain
        nfs:
          path: /mnt/test
          server: nfs.example.com
"""
        )

        try:
            # Apply persistent volume present state
            ret = salt_call_cli.run("state.apply", "test_persistent_volume")
            assert ret.returncode == 0
            assert ret.data
            # Check state result
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]
            assert test_pv in state_ret["changes"]

            # Verify persistent volume exists
            ret = salt_call_cli.run("kubernetes.show_persistent_volume", name=test_pv)
            assert ret.returncode == 0
            assert ret.data["metadata"]["name"] == test_pv
            assert ret.data["spec"]["capacity"]["storage"] == "1Gi"

            # Test idempotency
            ret = salt_call_cli.run("state.apply", "test_persistent_volume")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert not state_ret["changes"]
            assert "already exists" in state_ret["comment"]

        finally:
            # Test persistent volume removal
            state_file.write_text(
                f"""
remove_persistent_volume:
  kubernetes.persistent_volume_absent:
    - name: {test_pv}
"""
            )

            ret = salt_call_cli.run("state.apply", "test_persistent_volume")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]
            assert "kubernetes.persistent_volume" in state_ret["changes"]

            # Verify PV is gone with retry
            for _ in range(5):
                ret = salt_call_cli.run("kubernetes.show_persistent_volume", name=test_pv)
                if ret.data is None:
                    break
                time.sleep(2)
            else:
                pytest.fail("PersistentVolume was not deleted")

    def test_persistent_volume_present_with_template(self, salt_call_cli, state_tree):
        """Test persistent_volume_present state using template rendering"""
        test_pv = "salt-test-pv-template"

        # Create state file that uses direct spec configuration
        state_file = state_tree / "test_pv.sls"
        state_file.write_text(
            f"""
test_persistent_volume:
  kubernetes.persistent_volume_present:
    - name: {test_pv}
    - api_version: v1
    - kind: PersistentVolume
    - metadata:
        name: {test_pv}
        labels:
          type: nfs
    - spec:
        capacity:
          storage: 5Gi
        access_modes:
          - ReadWriteOnce
        persistent_volume_reclaim_policy: Retain
        storage_class_name: nfs
        nfs:
          path: /exports/test
          server: nfs.example.com
"""
        )

        try:
            # Apply state
            ret = salt_call_cli.run("state.apply", "test_pv")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]

            # Verify PV was created with correct values
            ret = salt_call_cli.run("kubernetes.show_persistent_volume", name=test_pv)
            assert ret.returncode == 0
            assert ret.data["metadata"]["name"] == test_pv
            assert ret.data["spec"]["capacity"]["storage"] == "5Gi"
            assert ret.data["spec"]["nfs"]["path"] == "/exports/test"
            assert ret.data["spec"]["access_modes"] == ["ReadWriteOnce"]

            # Test idempotency
            ret = salt_call_cli.run("state.apply", "test_pv")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert not state_ret["changes"]
            assert "already exists" in state_ret["comment"]

        finally:
            # Cleanup
            salt_call_cli.run("state.single", "kubernetes.persistent_volume_absent", name=test_pv)

    def test_persistent_volume_claim_present_absent(self, salt_call_cli, state_tree):
        """Test persistent volume claim creation and deletion via states"""
        test_pvc = "test-pvc-state"

        # Create PVC state file
        state_file = state_tree / "test_pvc.sls"
        state_file.write_text(
            f"""
test_persistent_volume_claim:
  kubernetes.persistent_volume_claim_present:
    - name: {test_pvc}
    - namespace: default
    - spec:
        access_modes:
          - ReadWriteOnce
        resources:
          requests:
            storage: 1Gi
        storage_class_name: standard
"""
        )

        # Apply initial PVC present state
        ret = salt_call_cli.run("state.apply", "test_pvc")
        assert ret.returncode == 0
        assert ret.data
        state_ret = ret.data[next(iter(ret.data))]
        assert state_ret["result"] is True
        assert state_ret["changes"]

        # Verify initial creation changes
        pvc_new = state_ret["changes"][f"default.{test_pvc}"]["new"]
        assert pvc_new["spec"]["resources"]["requests"]["storage"] == "1Gi"
        assert "ReadWriteOnce" in pvc_new["spec"]["access_modes"]

        # Test PVC removal
        state_file.write_text(
            f"""
remove_persistent_volume_claim:
  kubernetes.persistent_volume_claim_absent:
    - name: {test_pvc}
    - namespace: default
"""
        )

        ret = salt_call_cli.run("state.apply", "test_pvc")
        assert ret.returncode == 0
        assert ret.data
        state_ret = ret.data[next(iter(ret.data))]
        assert state_ret["result"] is True
        assert state_ret["changes"]
        assert "kubernetes.persistent_volume_claim" in state_ret["changes"]

        # Verify PVC is gone
        ret = salt_call_cli.run(
            "kubernetes.show_persistent_volume_claim", name=test_pvc, namespace="default"
        )
        assert ret.data is None

    def test_persistent_volume_claim_with_template(self, salt_call_cli, state_tree):
        """Test persistent volume claim creation using template"""
        test_pvc = "test-pvc-template"

        # Create template file
        template_file = state_tree / "pvc-template.yaml.jinja"
        template_file.write_text(
            """
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ name }}
  namespace: {{ namespace }}
  annotations:
    storage.kubernetes.io/storage-class: {{ storage_class }}
spec:
  access_modes:
    - {{ access_mode }}
  resources:
    requests:
      storage: {{ storage_size }}
"""
        )

        # Create state file using template
        state_file = state_tree / "test_pvc_template.sls"
        state_file.write_text(
            f"""
test_persistent_volume_claim:
  kubernetes.persistent_volume_claim_present:
    - name: {test_pvc}
    - namespace: default
    - source: salt://pvc-template.yaml.jinja
    - template: jinja
    - context:
        name: {test_pvc}
        namespace: default
        storage_size: 2Gi
        access_mode: ReadWriteOnce
        storage_class: standard
"""
        )

        try:
            # Apply state
            ret = salt_call_cli.run("state.apply", "test_pvc_template")
            assert ret.returncode == 0
            assert ret.data
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is True
            assert state_ret["changes"]

            # Verify changes structure
            pvc_changes = state_ret["changes"][f"default.{test_pvc}"]["new"]
            assert pvc_changes["spec"]["resources"]["requests"]["storage"] == "2Gi"
            assert "ReadWriteOnce" in pvc_changes["spec"]["access_modes"]

            # Wait briefly for the PVC to be fully created
            time.sleep(2)

            # Verify PVC was created correctly
            ret = salt_call_cli.run(
                "kubernetes.show_persistent_volume_claim", name=test_pvc, namespace="default"
            )
            assert ret.returncode == 0
            assert ret.data["metadata"]["name"] == test_pvc
            assert ret.data["spec"]["resources"]["requests"]["storage"] == "2Gi"

        finally:
            # Cleanup
            salt_call_cli.run(
                "state.single",
                "kubernetes.persistent_volume_claim_absent",
                name=test_pvc,
                namespace="default",
            )

    def test_persistent_volume_claim_test_mode(self, salt_call_cli, state_tree):
        """Test persistent volume claim states in test mode"""
        test_pvc = "test-pvc-testmode"

        # Create state file
        state_file = state_tree / "test_pvc.sls"
        state_file.write_text(
            f"""
test_persistent_volume_claim:
  kubernetes.persistent_volume_claim_present:
    - name: {test_pvc}
    - namespace: default
    - spec:
        access_modes:
          - ReadWriteOnce
        resources:
          requests:
            storage: 1Gi
"""
        )

        # Test create with test=True
        ret = salt_call_cli.run("state.apply", "test_pvc", test=True)
        assert ret.returncode == 0
        state_ret = ret.data[next(iter(ret.data))]
        assert state_ret["result"] is None
        assert "is going to be created" in state_ret["comment"]
        assert not state_ret["changes"]

        # Create PVC
        ret = salt_call_cli.run("state.apply", "test_pvc")
        assert ret.returncode == 0

        try:
            # Test delete with test=True
            state_file.write_text(
                f"""
remove_persistent_volume_claim:
  kubernetes.persistent_volume_claim_absent:
    - name: {test_pvc}
    - namespace: default
"""
            )

            ret = salt_call_cli.run("state.apply", "test_pvc", test=True)
            assert ret.returncode == 0
            state_ret = ret.data[next(iter(ret.data))]
            assert state_ret["result"] is None
            assert "is going to be deleted" in state_ret["comment"]
            assert not state_ret["changes"]

        finally:
            # Cleanup
            salt_call_cli.run(
                "state.single",
                "kubernetes.persistent_volume_claim_absent",
                name=test_pvc,
                namespace="default",
            )
