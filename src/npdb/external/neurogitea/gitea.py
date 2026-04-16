import gitea as gt_client
from base64 import b64encode

from npdb.managers.model import Manager


class GiteaManager(Manager):
    def __init__(self, url: str, user: str, token: str, ssl_verify: bool = True):
        self.client = gt_client.Gitea(
            gitea_url=url, token_text=token, verify=ssl_verify)
        self.git_auth = b64encode(
            f"{user}:{token}".encode("utf-8")).decode("ascii")

    def git_http_config(self):
        config = {
            "extraHeader": f"Authorization: Basic {self.git_auth}",
            "sslVerify": str(self.client.requests.verify).lower()
        }
        return [c for k, v in config.items() for c in ["-c", f"http.{k}={v}"]]
