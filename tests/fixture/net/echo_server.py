"""Echo server for the kit's topology tests: echoes every line a client sends.

    python3 echo_server.py --port 0 [--spawn-orphan PIDFILE] [--exit-early]

Prints "echo server listening on port N" once it accepts connections. --spawn-orphan starts a
child that sleeps for ten minutes and writes its pid, so a test can check the kit stops it too.
"""

import argparse
import socketserver
import subprocess
import sys
import time


class Echo(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            self.wfile.write(line)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--spawn-orphan", metavar="PIDFILE")
    parser.add_argument("--exit-early", action="store_true", help="exit with 3 before listening")
    args = parser.parse_args()
    if args.exit_early:
        print("echo server: refusing to start", flush=True)
        sys.exit(3)
    if args.spawn_orphan:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        with open(args.spawn_orphan, "w") as handle:
            handle.write(str(child.pid))
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer.daemon_threads = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", args.port), Echo) as server:
        print(f"echo server listening on port {server.server_address[1]}", flush=True)
        time.sleep(0.01)
        server.serve_forever()


if __name__ == "__main__":
    main()
