use serde::Serialize;
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);
const GITHUB_HOST: &str = "github.com:443";
const DOCKER_REGISTRY_HOST: &str = "registry-1.docker.io:443";

#[derive(Debug, Serialize)]
pub struct NetworkStatus {
    pub github_reachable: bool,
    pub docker_registry_reachable: bool,
    pub all_reachable: bool,
}

pub fn check() -> NetworkStatus {
    let github_reachable = is_reachable(GITHUB_HOST);
    let docker_registry_reachable = is_reachable(DOCKER_REGISTRY_HOST);
    NetworkStatus {
        github_reachable,
        docker_registry_reachable,
        all_reachable: github_reachable && docker_registry_reachable,
    }
}

fn is_reachable(host: &str) -> bool {
    match host.to_socket_addrs() {
        Ok(mut addrs) => addrs
            .next()
            .map(|addr| TcpStream::connect_timeout(&addr, CONNECT_TIMEOUT).is_ok())
            .unwrap_or(false),
        Err(_) => false,
    }
}
