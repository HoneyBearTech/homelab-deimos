# This stack's own steps in scripts/smoke-test.sh, which sources this file; it isn't run on its own. It may define
# smoke_prepare, run once the throwaway settings ($work/env) and folders exist, just before the stack starts (it may
# write $work/override-stack.yaml, which is then added to the Compose files), and smoke_check, run once every service
# is healthy and again after the backup has been restored. Both run with smoke-test.sh's variables and functions
# (work, project, root, busybox, compose, fail). Written for bash 3.2 and later (macOS ships 3.2).
# shellcheck shell=bash disable=SC2154 # work, project and busybox come from smoke-test.sh

# The Dozzle agent: a throwaway certificate and key, made with `dozzle generate-certs` as on the host and owned by root
# as there; then that the agent loaded them, accepts a hub presenting them and refuses one presenting the pair built
# into every Dozzle image.
dozzle_service=dozzle-agent

# The Dozzle image compose.yaml pins.
dozzle_image() { compose config --images "$dozzle_service"; }

# The folder the agent's secrets come from: CONFIG_ROOT/dozzle/certs.
dozzle_certs() { echo "$(sed -n 's/^CONFIG_ROOT=//p' "$work/env")/dozzle/certs"; }

smoke_prepare() {
  local certs
  certs=$(dozzle_certs)
  # CONFIG_ROOT belongs to PUID, who may not be the caller: the folder is made as root, inside CONFIG_ROOT (a missing
  # folder Docker Desktop creates for a mount would be in its VM, not on the Mac). generate-certs writes the key with
  # mode 0600 and the certificate with 0644, both owned by root (the user it runs as), as on the host.
  docker run --rm --network none -v "${certs%/dozzle/certs}:/config-root" "$busybox" \
    mkdir -p /config-root/dozzle/certs </dev/null
  docker run --rm --network none -v "$certs:/out" "$(dozzle_image)" --no-analytics generate-certs \
    --cert-out /out/dozzle_cert.pem --key-out /out/dozzle_key.pem </dev/null >/dev/null
}

# `dozzle agent-test` against the agent, as a hub on the stack's network would connect; with -v and -e options for
# docker run as arguments, the hub's certificate and key.
dozzle_agent_test() {
  docker run --rm --network "${project}_default" "$@" "$(dozzle_image)" --no-analytics agent-test \
    "$dozzle_service:7007" </dev/null 2>&1
}

smoke_check() {
  local logs out
  logs=$(compose logs --no-color "$dozzle_service")
  [[ $logs == *"Loaded custom dozzle certificate and key"* ]] ||
    fail "the Dozzle agent didn't load its certificate and key"
  [[ $logs != *"built-in certificate"* ]] || fail "the Dozzle agent uses the built-in certificate"
  out=$(dozzle_agent_test -v "$(dozzle_certs):/certs:ro" -e DOZZLE_CERT=/certs/dozzle_cert.pem \
    -e DOZZLE_KEY=/certs/dozzle_key.pem) || fail "the Dozzle agent refused a hub with its certificate and key: $out"
  out=$(dozzle_agent_test) && fail "the Dozzle agent accepted a hub with the built-in certificate"
  [[ $out == *"certificate"* ]] || fail "unexpected error from a hub with the built-in certificate: $out"
  echo "Dozzle agent: accepts a hub with its own certificate and key, refuses one with the built-in pair"
}
