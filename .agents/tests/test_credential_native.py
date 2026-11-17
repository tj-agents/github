from credential_acceptance import main


def test_native_credential_nuget_and_mcp_launchers_work_without_the_producer() -> None:
    assert main() == 0
