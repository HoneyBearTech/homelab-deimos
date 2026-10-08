# Verifying releases

Every homelab-deimos release is published by the [`release.yml`](../.github/workflows/release.yml)
workflow when a version tag is pushed. homelab-deimos builds no images: a release is a version of the Compose
file with every image pinned by digest. You can check that what you run came from that workflow, unchanged:

- the GitHub Release has a source archive (with the license), a **CycloneDX SBOM** listing every service's
  image and digest (`homelab-deimos-<version>.cdx.json`) and `SHA256SUMS` for both;
- `SHA256SUMS` is signed with [cosign](https://docs.sigstore.dev/) "keylessly": the signature
  (`SHA256SUMS.sigstore.json`) is tied to the workflow's GitHub identity through
  [Sigstore](https://www.sigstore.dev/), so there is no long-lived signing key to steal, and every
  signature is recorded in the public Rekor transparency log;
- **SLSA build provenance** covers every file in `SHA256SUMS`
  (`homelab-deimos-<version>.provenance.sigstore.json`, also stored by GitHub, and as in-toto JSON Lines in
  `homelab-deimos-<version>.intoto.jsonl`);
- the **version tag** in git is signed with the maintainer's SSH key.

You need [cosign](https://docs.sigstore.dev/cosign/system_config/installation/) 3.0 or later. The examples
use version 0.1.0; substitute the one you run.

## The release files

Download `SHA256SUMS`, `SHA256SUMS.sigstore.json`, the SBOM and the source archive from the
[release page](https://github.com/HoneyBearTech/homelab-deimos/releases), then:

```sh
cosign verify-blob SHA256SUMS --bundle SHA256SUMS.sigstore.json \
  --certificate-identity-regexp '^https://github\.com/HoneyBearTech/homelab-deimos/\.github/workflows/release\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
sha256sum -c SHA256SUMS
```

The first command proves `SHA256SUMS` came from the release workflow; the second, that the archive and
SBOM match it.

## Build provenance

With the [GitHub CLI](https://cli.github.com/):

```sh
gh attestation verify homelab-deimos-0.1.0.tar.gz --repo HoneyBearTech/homelab-deimos \
  --signer-workflow HoneyBearTech/homelab-deimos/.github/workflows/release.yml
```

To verify offline, add `--bundle homelab-deimos-0.1.0.provenance.sigstore.json`.

## The git tag

The maintainer signs version tags with an SSH key whose public half is in
[`.github/allowed_signers`](../.github/allowed_signers):

```sh
git clone https://github.com/HoneyBearTech/homelab-deimos.git && cd homelab-deimos
git -c gpg.ssh.allowedSignersFile=.github/allowed_signers tag -v v0.1.0
```

It should print `Good "git" signature for 31805425+HoneyBearTech@users.noreply.github.com`. Check
`.github/allowed_signers` against the key published at <https://github.com/HoneyBearTech.keys> rather than
trusting the copy in the repository alone.

## The images

The images are signed (or not) by their own publishers. What homelab-deimos guarantees is that the digests
in the verified SBOM and `compose.yaml` are the ones that were reviewed: `docker compose pull` fetches
exactly those bytes, whatever a registry tag points to later. To compare your checkout with the release:

```sh
docker compose config --images | sort   # the pinned images your compose.yaml resolves to
jq -r '.components[] | "\(.name):\(.version)@sha256:\(.hashes[0].content)"' homelab-deimos-0.1.0.cdx.json | sort
```

The two lists match, except that the SBOM may spell out a registry's implied prefix (such as `docker.io/`).
