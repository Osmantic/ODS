package main

import (
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
)

func TestHealthAndAuthenticationBoundary(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		u, p, ok := r.BasicAuth()
		if !ok || u != "ods" || p != "secret" {
			w.Header().Set("WWW-Authenticate", `Basic realm="MicroBin"`)
			w.WriteHeader(401)
			return
		}
		w.Write([]byte("private content"))
	}))
	defer upstream.Close()
	target, _ := url.Parse(upstream.URL)
	h := handler(target, "ods", "secret")
	for _, tc := range []struct {
		path   string
		status int
		body   string
	}{{"/_ods/health", 200, "Ready\n"}, {"/", 401, ""}, {"/upload", 401, ""}} {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("GET", tc.path, nil))
		if w.Code != tc.status || w.Body.String() != tc.body {
			t.Fatalf("%s: %d %q", tc.path, w.Code, w.Body.String())
		}
	}
	w := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/", nil)
	req.SetBasicAuth("ods", "secret")
	h.ServeHTTP(w, req)
	if w.Code != 200 || w.Body.String() != "private content" {
		t.Fatal("client authentication was not forwarded")
	}
	w = httptest.NewRecorder()
	handler(target, "ods", "wrong").ServeHTTP(w, httptest.NewRequest("GET", "/_ods/health", nil))
	if w.Code != 503 {
		t.Fatal("bad credentials reported healthy")
	}
}

func TestHealthRejectsRedirectAndFailure(t *testing.T) {
	for _, status := range []int{302, 500} {
		upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Location", "/private")
			w.WriteHeader(status)
		}))
		target, _ := url.Parse(upstream.URL)
		w := httptest.NewRecorder()
		handler(target, "ods", "secret").ServeHTTP(w, httptest.NewRequest("GET", "/_ods/health", nil))
		upstream.Close()
		if w.Code != 503 {
			t.Fatalf("upstream %d accepted", status)
		}
	}
}
