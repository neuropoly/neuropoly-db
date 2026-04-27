import gitea as gt_client
from tenacity import retry, stop_after_attempt, wait_exponential


class OrganizationMixin:
    def __init__(self, organization: str, client: gt_client.Gitea):
        self.organization = self._fetch_organization(client, organization)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
    def _fetch_organization(self, client: gt_client.Gitea, organization: str):
        return gt_client.Organization.request(client, organization)

    @property
    def datasets(self):
        return self._fetch_repositories()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
    def _fetch_repositories(self):
        return self.organization.get_repositories()
