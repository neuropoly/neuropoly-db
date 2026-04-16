import gitea as gt_client


class OrganizationMixin:
    def __init__(self, organization: str, client: gt_client.Gitea):
        self.organization = gt_client.Organization.request(
            client, organization)

    @property
    def datasets(self):
        return self.organization.get_repositories()
