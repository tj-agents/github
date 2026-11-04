from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReleaseArtifact:
    archive_name: str
    binary_name: str
    archive_sha256: str
    binary_sha256: str


WINDOWS_X64 = ReleaseArtifact(
    "github-mcp-server_Windows_x86_64.zip",
    "github-mcp-server.exe",
    "ddce274916ebf31346dee71a0ed3345e80cd36b060a0bfb8160db0afc411e9d3",
    "d9cb8667172b995c49db7f7da0feb7b8dd9cb5d20224baaff2c84a9e8d512967",
)

LINUX_X64 = ReleaseArtifact(
    "github-mcp-server_Linux_x86_64.tar.gz",
    "github-mcp-server",
    "a442f9db287cea01008bcb55fe6bc0292270301046c3edbd1a8b22d6e4157b2b",
    "cdd2dfbf269ca249f7e31148e20d072a22e224dd7cab0d5a176d146900b85324",
)
