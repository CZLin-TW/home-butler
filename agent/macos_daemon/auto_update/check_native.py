"""Compile/run fake native tests only; no Keychain, installation or network."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

SOURCE = Path(__file__).resolve().parent
sys.path.insert(0, str(SOURCE.parent))
from settings import load, swift_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True)
    config = load(SOURCE.parent / "config.example.json")
    constants = {"requirement": "FAKE", "version": "a" * 40,
                 "originalSender": "/FAKE", "originalCDHash": "b" * 40}
    swift = swift_settings(config) + "\nenum UpdateTrust {\n" + "\n".join(
        "static let " + k + " = " + json.dumps(v) for k, v in constants.items()) + "\n}\n"
    swift += (SOURCE.parent / "sender.swift").read_text() + "\n" + (SOURCE / "trust.swift").read_text()
    source = args.output / "main.swift"
    source.write_text(swift)
    binary = args.output / "fake-tests"
    subprocess.run(["/usr/bin/swiftc", "-D", "TESTING", "-D", "AUTO_UPDATE", "-module-cache-path",
                    str(args.output / "cache"), str(source), "-o", str(binary)], check=True)
    subprocess.run([str(binary.resolve())], check=True)
    # Compile the real root guard and private-key import API without executing it.
    subprocess.run(["/usr/bin/swiftc", "-module-cache-path", str(args.output / "cache"),
                    str(SOURCE / "signer.swift"), "-o", str(args.output / "signer")], check=True)


if __name__ == "__main__":
    main()
