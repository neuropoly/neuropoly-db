# NEUROPOLY DATABASE MANAGEMENT

Everything you need to manage databases at the NeuroPoly lab ! Well, not really ... but at least you can get a bird's eye view of everything
available to you, then ask for access to it when needed. This repository doesn't let you **download** or **upload** data inside the databases,
but it gives you a complete view of the **metadata contained in those databases**, and optionally also provides **links to the data** for users
already authenticated with it.

## Databases

The databases available for preview through this service are:

- **NeuroPoly BIDS Database**

  The full set of BIDS datasets available at the NeuroPoly lab is **indexed in a Neurobagel node**. It is globaly
  accessible for perusing, but provides download links - usable by authenticated users only - to the **git-annexed
  storage endpoint**. Basically, you get a full view of `data.neuro.polymtl.ca`, **but you can't access it directly**.

## Installation

### NeuroPoly DB

To get the base **NeuroPoly DB** management stack up and running, you only need to install `uv`, **we handle everything else** :

```bash
curl -L https://astral.sh/uv/install.sh | sh
uv venv .venv
uv sync --activate
```

> [!WARNING]
> The above command **might fail if some virtual environment has already been configured in the provided directory (`.venv`)**. If you experience
> issues, simply **delete the content** under the virtual environment's directory and **re-run the command**.

### Neurobagel

**You need access to a Neurobagel node**. If deploying in production, or for external use outside the **NeuroPoly database use-case**, deploy
your own node, using the [Neurobagel documentation](https://neurobagel.org/user_guide/getting_started/). **Else, we recommend you use the
`devcontainer` provided in this repository**, which is pre-configured to setup a fully capable Neurobagel node for development purposes, tied
to your environment, with UIs directly accessible in your browser. To use the `devcontainer`, simply open this repository in a compatible code editor (like VSCode), and open the `devcontainer` when prompted. It will automatically install all dependencies, and start the Neurobagel node for you. There, all neurobagel services are accessible on a common `gateway` under **localhost**. Inspect the **VSCode forwarded ports** to find the right port to access the Neurobagel UI.
