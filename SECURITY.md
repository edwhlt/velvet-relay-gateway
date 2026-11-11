# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Use GitHub's private reporting instead: open the **Security** tab of this repository and choose **Report a vulnerability**.

Include what you found, how to reproduce it, and the version (commit) you tested. Never include real keys, tokens or addresses: the output of `velvet-diag` is designed to be safe to share.

## Scope

In scope: the Ansible playbook and templates, the firewall rules, the `velvet-exit-api` service, and the helper scripts in this repository.

Out of scope: vulnerabilities in WireGuard, Tor, Docker or the operating system themselves. Please report those upstream.

## Supported versions

Only the latest commit on `main` is supported.

## Trust model

Read [What this does and does not protect](README.md#what-this-does-and-does-not-protect) before reporting: some behaviours, such as any connected device being able to manage the others, are documented design choices rather than vulnerabilities.
