from prefect import flow, task
from prefect.logging import get_logger
import os

logger = get_logger(__name__)

@task
def read_keyvault_secret(secret_name: str):
    # The CSI driver mounts secrets as files at this path
    # This path must match the volumeMounts.mountPath in your YAML
    secret_path = f"/mnt/secrets-store/{secret_name}"

    if not os.path.exists(secret_path):
        raise FileNotFoundError(f"Secret {secret_name} not found at {secret_path}. "
                                "Check if the CSI volume is mounted correctly.")

    with open(secret_path, "r") as f:
        secret_value = f.read().strip()

    return secret_value

@flow(name="Azure Key Vault CSI Flow")
def my_keyvault_flow(secret_name: str = "chris-secret"):
    logger.info("Fetching secret: ***")

    val = read_keyvault_secret(secret_name)

    # Process your secret (e.g., use it for an API call)
    logger.info(f"Successfully retrieved secret of length: {len(val)}")

if __name__ == "__main__":
    my_keyvault_flow()
