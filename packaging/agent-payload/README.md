# Agent payload

The Windows agent executable that ships *inside the server image*.

This directory is filled by CI: the `executables` job builds
`openpatch-agent.exe` on Windows, and the `image` job downloads that artefact
into here before building the image (see `.github/workflows/ci.yml`).

To do the same locally, before building the image from source:

    python packaging/build.py agent
    copy dist\openpatch-agent.exe packaging\agent-payload\

An agent built this way carries no CA so it pairs with the `ca.crt` the bundle also contains, 
and the bundle's installer wires the two together.

An agent built from a checkout that *does* have `server/certs/ca.crt` has the
authority compiled in it. To hand that one out, put  it here and build the image: 
this directory is the only way an executable reaches a bundle.