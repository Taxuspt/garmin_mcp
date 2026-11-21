#!/usr/bin/env python3
"""Smoke-test a built garmin-mcp image without Garmin credentials.

Starts the image twice and checks that it speaks MCP and lists its tools:

* streamable-http: /healthz answers, initialize + tools/list over /mcp, and
  the image HEALTHCHECK turns the container healthy.
* stdio (the image default): initialize + tools/list over stdin/stdout.

Garmin login runs on a background thread and fails quietly without tokens or
credentials, so neither check touches Garmin's servers.

Usage: python3 scripts/docker_smoke_test.py IMAGE
Standard library only, so CI can run it without installing anything.
"""

import contextlib
import json
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

EXPECTED_TOOL = "get_stats"
TIMEOUT = 60

INITIALIZE_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "docker-smoke-test", "version": "0"},
    },
}
INITIALIZED_NOTIFICATION = {"jsonrpc": "2.0", "method": "notifications/initialized"}
LIST_TOOLS_REQUEST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


class ContainerExited(Exception):
    """The container stopped, so polling it any longer is pointless."""


def docker(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout.strip()


@contextlib.contextmanager
def container_name():
    """Yield a unique container name; on failure print the container's logs,
    and always remove the container afterwards."""
    name = f"garmin-mcp-smoke-{uuid.uuid4().hex[:8]}"
    try:
        yield name
    except BaseException:
        # The server logs to stderr; let `docker logs` pass both streams through.
        subprocess.run(["docker", "logs", name], stdout=sys.stderr, stderr=sys.stderr)
        raise
    finally:
        subprocess.run(["docker", "rm", "--force", name], capture_output=True)


def wait_until(what: str, check, timeout: float = TIMEOUT):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except ContainerExited:
            raise
        except Exception as exc:  # noqa: BLE001 - keep polling until the deadline
            last_error = exc
        time.sleep(1)
    raise TimeoutError(f"timed out waiting for {what}" + (f": {last_error}" if last_error else ""))


def ensure_running(container: str) -> None:
    if docker("inspect", "--format", "{{.State.Running}}", container) != "true":
        raise ContainerExited(f"container {container} exited")


def wait_healthy(container: str) -> None:
    def healthy() -> bool:
        status = docker("inspect", "--format", "{{.State.Health.Status}}", container)
        if status == "unhealthy":
            raise RuntimeError("container reported unhealthy")
        return status == "healthy"

    wait_until("HEALTHCHECK to report healthy", healthy)


def count_tools(response: dict) -> int:
    assert response.get("id") == LIST_TOOLS_REQUEST["id"], response
    tools = [tool["name"] for tool in response["result"]["tools"]]
    assert EXPECTED_TOOL in tools, f"{EXPECTED_TOOL} missing from {len(tools)} tools"
    return len(tools)


def post_mcp(url: str, message: dict, session: "str | None") -> "tuple[dict | None, str | None]":
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if session:
        headers["Mcp-Session-Id"] = session
    request = urllib.request.Request(url, data=json.dumps(message).encode(), headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        session = response.headers.get("Mcp-Session-Id", session)
        body = response.read().decode()
        if "id" not in message:
            return None, session
        if response.headers.get_content_type() == "text/event-stream":
            for line in body.splitlines():
                if line.startswith("data:"):
                    payload = json.loads(line[len("data:"):])
                    if payload.get("id") == message["id"]:
                        return payload, session
            raise AssertionError(f"no reply to id {message['id']} in event stream: {body!r}")
        return json.loads(body), session


def check_http(image: str) -> None:
    with container_name() as container:
        docker(
            "run", "--detach", "--name", container,
            "--env", "GARMIN_MCP_TRANSPORT=streamable-http",
            "--env", "GARMIN_MCP_HOST=0.0.0.0",
            "--publish", "127.0.0.1::8000",
            image,
        )
        port = docker("port", container, "8000/tcp").splitlines()[0].rsplit(":", 1)[1]
        base = f"http://127.0.0.1:{port}"

        def healthz_ok() -> bool:
            ensure_running(container)
            return urllib.request.urlopen(f"{base}/healthz", timeout=5).status == 200

        wait_until("/healthz", healthz_ok)

        reply, session = post_mcp(f"{base}/mcp", INITIALIZE_REQUEST, None)
        assert reply and "result" in reply, reply
        post_mcp(f"{base}/mcp", INITIALIZED_NOTIFICATION, session)
        reply, _ = post_mcp(f"{base}/mcp", LIST_TOOLS_REQUEST, session)
        count = count_tools(reply)

        wait_healthy(container)
        print(f"streamable-http: ok ({count} tools, HEALTHCHECK healthy)")


def check_stdio(image: str) -> None:
    with container_name() as container:
        # stderr is discarded here rather than piped (an unread pipe can fill up
        # and stall the server); on failure container_name() prints it from
        # `docker logs`, which is why the container isn't started with --rm.
        proc = subprocess.Popen(
            ["docker", "run", "--interactive", "--name", container, image],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        replies: dict = {}

        def read_stdout() -> None:
            for line in proc.stdout:
                message = json.loads(line)
                if "id" in message:
                    replies[message["id"]] = message

        threading.Thread(target=read_stdout, daemon=True).start()
        try:
            for message in (INITIALIZE_REQUEST, INITIALIZED_NOTIFICATION, LIST_TOOLS_REQUEST):
                proc.stdin.write(json.dumps(message) + "\n")
                proc.stdin.flush()
                if "id" in message:
                    wait_until(
                        f"stdio reply to {message['method']}",
                        lambda: message["id"] in replies or proc.poll() is not None,
                    )
                    if message["id"] not in replies:
                        raise ContainerExited(f"container {container} exited before replying to {message['method']}")
            count = count_tools(replies[LIST_TOOLS_REQUEST["id"]])

            wait_healthy(container)
            print(f"stdio: ok ({count} tools, HEALTHCHECK healthy)")
        except BaseException:
            subprocess.run(["docker", "kill", container], capture_output=True)
            raise
        finally:
            proc.stdin.close()
            proc.wait(timeout=30)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    image = sys.argv[1]
    check_http(image)
    check_stdio(image)


if __name__ == "__main__":
    main()
