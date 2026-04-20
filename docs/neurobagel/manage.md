# NeuroBagel node management

## Update the datasets available

Place the new datasets (`.jsonld` files) in the `./seed-datasets` directory at the root of the repository, **or the directory set in the `LOCAL_GRAPH_DATA` variable, if you edited the `.env` file**.

Once done, re-deploy the node with :

```bash
docker compose restart
```

## Hot-reloading

To avoid re-deploying the node every time you want to update the datasets, you can use the hot-reloading feature of NeuroBagel. This feature allows you to update the datasets without stopping the node.

```bash
docker compose restart init_data
echo "Waiting for init_data to complete..." && sleep 5
docker compose restart graph api
echo "Waiting for graph and api services to restart..." && sleep 20
```
