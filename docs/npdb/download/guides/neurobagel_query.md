# NeuroBagel querying

## Query User Interface

NeuroBagel exposes a user-friendly web interface to query and explore the datasets available in the node. Use the URL `http://localhost:9000` to access the interface (replace the port number with the value of `NB_QUERY_PORT_HOST` if you changed it in the `.env` file).

Once open, **click on `Submit Query`** to display the full list of available datasets.

![NeuroBagel query interface](../../../assets/neurobagel_query/neurobagel_query_all.png)

### Refining the query results

Use the **filters stacked on the left of the interface** to refine the query results using _age ranges_, _sex_, _diagnoses_, _imaging modalities_ and more. Once satisfied, **click on the `Submit Query`** button again.

Datasets matching the selected filters will be displayed on the right, with the number of matching subjects they contain, as well as the list of available data modalities associated to their imaging sessions.

![NeuroBagel query interface with filters](../../../assets/neurobagel_query/neurobagel_query_subset.png)

### Exporting the query results

Use the **tick boxes on the left of each dataset card** to select the datasets to export. This activates the **Download** button at the bottom right of the interface.

![NeuroBagel query interface with filters](../../../assets/neurobagel_query/neurobagel_query_export.png)

The exported query results is saved in a **T**ab-**S**eparated-**V**alue (**TSV**) file, with one line per subject and imaging/phenotypic session. In it you'll find most of the metadata associated with the subjects matching the query, like their age, sex, diagnosis, etc. The table below describes some interesting columns, aside `sex`, `age` and `diagnosis` :

|                            |                                                                                            |
|----------------------------|--------------------------------------------------------------------------------------------|
|        `DatasetName`       | Name of the dataset the subject belongs to.                                                |
|       `RepositoryURL`      | URL of the repository hosting the dataset.                                                 |
|         `SubjectID`        | Identifier of the subject.                                                                 |
|         `SessionID`        | Identifier of the imaging/phenotypic session.                                              |
|    `ImagingSessionPath`    | Relative path to the imaging session in the repository.                                    |
| `SessionImagingModalities` | Name of the imaging modalities available in the session (e.g. `T1w`, `T2w`, `fMRI`, etc.). |
|        `AccessLink`        | Link to access the session data.                                                           |

### Downloading the imaging data from the query results

#### Prerequisites

> [!WARNING]
> Before using the download workflow, install the `all` dependency group if you have not done so already. This will install all possible download backends that could be included in your Neurobagel query result:
>
> ```bash
> uv sync --active --group all
> ```
>
> This is required for the client access used by the dataset download commands.

> [!IMPORTANT]
> Setup your [NeuroGitea](https://data.neuro.polymtl.ca) account for automated access, following [these instructions](../../../neurogitea/account.md#neurogitea-account-setup).

The exported query results associates an `AccessLink` and/or a `RepositoryURL` to each dataset. The `npdb download` command line tool will automatically determine the best method to use and download the datasets in your current directory (use `--output-dir <path>` to specify a different output directory):

```bash
uv run npdb download --no-verify-ssl <query-results.tsv>
```

> [!WARNING]
> The `--no-verify-ssl` option is required until a **chain of trust** is established for the NeuroGitea server. The potential vulnerabilities of this option are mitigated by obligatory VPN activation.
