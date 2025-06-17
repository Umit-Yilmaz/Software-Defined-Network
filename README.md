
### Dockerized Ryu Controller 

This section outlines how to set up and run a Ryu SDN controller within a Docker container.

**Purpose:** Containerizing Ryu simplifies deployment, ensures consistent environments, and isolates the controller from the host system's dependencies.

Note: Ryu's default OpenFlow port (6653) and REST API port (8080) -  EXPOSE 6653 8080


**Building the Docker Image:**

To build the Docker image named `my-ryu-controller` from your `Dockerfile` in the current directory:

```bash
docker build -t my-ryu-controller .
```

**Running the Docker Container:**

To run a detached Docker container named `ryu-controller-rest`, mapping ports 6653 (OpenFlow) and 8080 (REST API) from the container to the host:

```bash
docker run -d -p 6653:6653 -p 8080:8080 --name ryu-controller-rest my-ryu-controller
```

**Managing the Container:**

* **Stop:** `docker stop ryu-controller-rest`
* **Remove:** `docker rm ryu-controller-rest`
* **List running containers:** `docker ps`
* **List all containers (including stopped):** `docker ps -a`
* **View logs:** `docker logs ryu-controller-rest`

---

### `network.py` (Mininet Topology Script) Brief Documentation

This Python script defines a Mininet topology, including clients, servers, and Open vSwitch (OVS) switches, and connects them to a remote Ryu controller.

**Purpose:**
This script creates a simulated network environment using Mininet to test SDN applications running on a remote controller (in this case, your Dockerized Ryu instance).

**Key Components:**

* **`Mininet`**: The core Mininet object for creating the network.
* **`RemoteController`**: Specifies that Mininet should connect to an external controller.
* **`OVSSwitch`**: Uses Open vSwitch as the software switch implementation, supporting OpenFlow 1.3.
* **Hosts (`c1`, `c2`, `sva`, `svb`)**: Represent end-devices (clients and servers) in the network. IPs are assigned.
* **Switches (`s1`, `s2`, `s3`)**: Represent OpenFlow-enabled switches that will be controlled by Ryu. They are configured to use OpenFlow 1.3.
* **Links**: Connect hosts to switches and switches to each other, forming the network topology.

**Topology Structure:**

```
[c1] -- [s1] -- [s2] -- [s3] -- [sva]
[c2] ---/            \--- [svb]
```
* `c1`, `c2`: Clients connected to `s1`.
* `sva`, `svb`: Video servers connected to `s3`.
* `s1`, `s2`, `s3`: Switches forming a simple backbone.

**Controller Connection:**

The script explicitly connects to a remote controller at `127.0.0.1:6653`. This IP address `127.0.0.1` (localhost) refers to the host machine from Mininet's perspective. Since your Docker container maps port 6653 to the host's 6653, Mininet can reach the Ryu controller through this mapping.

```python
net.addController(name='c0', controller=RemoteController, ip="127.0.0.1", port=6653)
```

**Usage:**

1.  Ensure Mininet is installed and your Ryu controller (e.g., Dockerized Ryu) is running and accessible on port 6653.
2.  Run the script with `sudo` privileges:
    ```bash
    sudo python3 network.py
    ```
3.  After the network starts, a `mininet>` prompt will appear. You can use Mininet CLI commands (e.g., `pingall`, `nodes`, `net`) to interact with the topology.
4.  The `input()` call at the end keeps the Mininet topology running until you press Enter, allowing you to interact with the network or the Ryu controller.

---

### Now, back to the `ws_topology` debugging:

Please proceed with the steps I outlined in my previous response to get the Ryu version from the container logs:

1.  **Stop and remove the existing container:**
    ```bash
    docker stop ryu-controller-rest
    docker rm ryu-controller-rest
    ```

2.  **Modify your `Dockerfile`** to add `RUN ryu-manager --version` before the `CMD` instruction.

3.  **Rebuild the Docker image:**
    ```bash
    docker build -t my-ryu-controller .
    ```

4.  **Run the new container:**
    ```bash
    docker run -d -p 6653:6653 -p 8080:8080 --name ryu-controller-rest my-ryu-controller
    ```

5.  **Get the logs for the Ryu version:**
    ```bash
    docker logs ryu-controller-rest
    ```

