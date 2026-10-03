package main

import (
	"context"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"time"
)

func handler(target *url.URL, username, password string) http.Handler {
	proxy := httputil.NewSingleHostReverseProxy(target)
	proxy.Transport = &http.Transport{Proxy: nil, ResponseHeaderTimeout: 30 * time.Second}
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) { http.Error(w, "Service unavailable", 503) }
	client := &http.Client{Timeout: 4 * time.Second, Transport: &http.Transport{Proxy: nil}, CheckRedirect: func(r *http.Request, via []*http.Request) error { return http.ErrUseLastResponse }}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/_ods/health" {
			proxy.ServeHTTP(w, r)
			return
		}
		w.Header().Set("Cache-Control", "no-store")
		if r.Method != http.MethodGet {
			w.WriteHeader(405)
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 4*time.Second)
		defer cancel()
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, target.String()+"/", nil)
		req.SetBasicAuth(username, password)
		resp, err := client.Do(req)
		if err != nil {
			http.Error(w, "Unavailable", 503)
			return
		}
		defer resp.Body.Close()
		if resp.StatusCode != 200 {
			http.Error(w, "Unavailable", 503)
			return
		}
		w.WriteHeader(200)
		_, _ = w.Write([]byte("Ready\n"))
	})
}

func main() {
	if len(os.Args) == 2 && os.Args[1] == "healthcheck" {
		c := http.Client{Timeout: 5 * time.Second}
		r, e := c.Get("http://127.0.0.1:8080/_ods/health")
		if e != nil {
			os.Exit(1)
		}
		defer r.Body.Close()
		if r.StatusCode != 200 {
			os.Exit(1)
		}
		return
	}
	password := os.Getenv("MICROBIN_ACCESS_PASSWORD")
	if password == "" {
		log.Fatal("MICROBIN_ACCESS_PASSWORD is required")
	}
	target, _ := url.Parse("http://microbin-backend:8080")
	s := http.Server{Addr: ":8080", Handler: handler(target, "ods", password), ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 1 << 20}
	log.Fatal(s.ListenAndServe())
}
