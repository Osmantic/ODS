package main

import (
    "net/http"
    "os"
    "time"
)

func main() {
    client := &http.Client{Timeout: 5 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
    response, err := client.Get("http://127.0.0.1:13133/status")
    if err != nil { os.Exit(1) }
    defer response.Body.Close()
    if response.StatusCode != http.StatusOK { os.Exit(1) }
}
