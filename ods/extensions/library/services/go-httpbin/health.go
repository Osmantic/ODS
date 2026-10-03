// The upstream distroless image has no shell, curl or wget.
package main

import (
	"net/http"
	"os"
	"time"
)

func main() {
	client := &http.Client{
		Timeout: 4 * time.Second,
		Transport: &http.Transport{Proxy: nil},
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	response, err := client.Get("http://127.0.0.1:8080/status/200")
	if err != nil {
		os.Exit(1)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusOK {
		os.Exit(1)
	}
}
