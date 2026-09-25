// ODS access adapter for LocalRecall's authenticated API and browser interface.
package main

import (
	"context"
	"crypto/subtle"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"
)

func same(a, b string) bool { return subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1 }

func handler(key string, backend *url.URL) http.Handler {
	proxy := httputil.NewSingleHostReverseProxy(backend)
	director := proxy.Director
	proxy.Director = func(r *http.Request) {
		director(r)
		r.Host = backend.Host
		r.Header.Set("Authorization", "Bearer "+key)
	}
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		http.Error(w, "LocalRecall is unavailable", http.StatusBadGateway)
	}
	probe := &http.Client{Timeout: 5 * time.Second, CheckRedirect: func(r *http.Request, via []*http.Request) error { return http.ErrUseLastResponse }}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		if r.URL.Path == "/health" {
			if r.Method != http.MethodGet && r.Method != http.MethodHead {
				w.WriteHeader(http.StatusMethodNotAllowed)
				return
			}
			// Public liveness contains no collection names or credentials. Actual data
			// routes below always require authentication, including HTML and assets.
			req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, backend.String()+"/api/collections", nil)
			if err != nil {
				w.WriteHeader(http.StatusServiceUnavailable)
				return
			}
			req.Header.Set("Authorization", "Bearer "+key)
			resp, err := probe.Do(req)
			if err != nil {
				w.WriteHeader(http.StatusServiceUnavailable)
				return
			}
			defer resp.Body.Close()
			if resp.StatusCode != http.StatusOK {
				w.WriteHeader(http.StatusServiceUnavailable)
				return
			}
			w.WriteHeader(http.StatusNoContent)
			return
		}
		authorized := same(r.Header.Get("Authorization"), "Bearer "+key)
		if user, password, ok := r.BasicAuth(); ok {
			authorized = same(user, "ods") && same(password, key)
		}
		if !authorized {
			w.Header().Set("WWW-Authenticate", `Basic realm="ODS LocalRecall", charset="UTF-8"`)
			http.Error(w, "Authentication required", http.StatusUnauthorized)
			return
		}
		if origin := r.Header.Get("Origin"); origin != "" && r.Method != http.MethodGet && r.Method != http.MethodHead {
			parsed, err := url.Parse(origin)
			if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || !strings.EqualFold(parsed.Host, r.Host) {
				http.Error(w, "Cross-origin write rejected", http.StatusForbidden)
				return
			}
		}
		r.Body = http.MaxBytesReader(w, r.Body, 128<<20)
		proxy.ServeHTTP(w, r)
	})
}

func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		client := &http.Client{Timeout: 8 * time.Second}
		response, err := client.Get("http://127.0.0.1:8080/health")
		if err != nil {
			os.Exit(1)
		}
		defer response.Body.Close()
		if response.StatusCode != http.StatusNoContent {
			os.Exit(1)
		}
		return
	}
	key := os.Getenv("LOCALRECALL_API_KEY")
	if len(key) < 24 || len(key) > 4096 || strings.ContainsAny(key, ",\r\n\t ") || strings.IndexFunc(key, func(r rune) bool { return r < 33 || r > 126 }) >= 0 {
		log.Fatal("Configure a LocalRecall API key of 24 or more non-whitespace characters")
	}
	backend, _ := url.Parse("http://localrecall-backend:8080")
	server := &http.Server{Addr: ":8080", Handler: handler(key, backend), ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second, WriteTimeout: 300 * time.Second, MaxHeaderBytes: 32768, ErrorLog: log.New(io.Discard, "", 0)}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		server.Shutdown(shutdown)
	}()
	fmt.Println("ODS LocalRecall access adapter listening")
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal("LocalRecall access adapter stopped unexpectedly")
	}
}
