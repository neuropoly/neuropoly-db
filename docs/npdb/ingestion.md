# Database ingestion procedure

## Neurogitea indexed databases

1. If not already done, copy the `template.env` file to a new `.env` file at the root of the repository :

   ```bash
   cp template.env .env
   ```

2. Edit the `.env` file to set your access credentials to the **Neurogitea database** you want to ingest from :

   - `NP_GITEA_APP_USER` : username to access Neurogitea.
   - `NP_GITEA_APP_TOKEN` : access token associated with the above username.
   - `NP_GITEA_APP_URL` : URL to the Neurogitea instance hosting the database.

3. Run the **dataset ingestion command** :

   ```bash
   npdb gitea2bagel <dataset_id> <output_directory>
   ```

   where :

   - `<dataset_id>` is the identifier of the dataset to ingest, as indexed in Neurogitea.
   - `<output_directory>` is the path where to output the `JSON-LD` files structured for ingestion by the NeuroBagel node.
