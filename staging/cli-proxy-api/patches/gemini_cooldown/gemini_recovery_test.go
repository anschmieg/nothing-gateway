package auth

import (
	"context"
	"net/http"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/router-for-me/CLIProxyAPI/v8/internal/registry"
	cliproxyexecutor "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/executor"
)

func TestGeminiCreditCooldownScopeExpiryAndRecovery(t *testing.T) {
	withQuotaCooldownEnabled(t)
	m := NewManager(nil, &RoundRobinSelector{}, nil)
	ctx := context.Background()
	for _, id := range []string{"gemini-depleted", "gemini-healthy"} {
		registry.GetGlobalRegistry().RegisterClient(id, "gemini", []*registry.ModelInfo{{ID: "gemini-3.8-flash"}, {ID: "gemini-sibling"}})
		t.Cleanup(func() { registry.GetGlobalRegistry().UnregisterClient(id) })
		if _, err := m.Register(ctx, &Auth{ID: id, Provider: "gemini", Status: StatusActive}); err != nil {
			t.Fatal(err)
		}
	}
	before := time.Now()
	retry := 30 * time.Minute
	m.MarkResult(ctx, Result{AuthID: "gemini-depleted", Provider: "gemini", Model: "gemini-3.8-flash", CredentialScope: true, RetryAfter: &retry, Error: &Error{HTTPStatus: 402, Message: "prepayment credits depleted"}})
	a, _ := m.GetByID("gemini-depleted")
	if a.Disabled || a.Status == StatusDisabled || a.Quota.Reason != "credential_quota" || a.Quota.NextRecoverAt.Before(before.Add(retry)) {
		t.Fatalf("not a finite credential cooldown: %+v", a)
	}
	for _, model := range []string{"gemini-3.8-flash", "gemini-sibling"} {
		if blocked, _, _ := isAuthBlockedForModel(a, model, before.Add(time.Minute)); !blocked {
			t.Fatal("sibling not blocked", model)
		}
		if blocked, _, _ := isAuthBlockedForModel(a, model, before.Add(31*time.Minute)); blocked {
			t.Fatal("credential permanently blocked", model)
		}
	}
	healthy, _ := m.GetByID("gemini-healthy")
	if blocked, _, _ := isAuthBlockedForModel(healthy, "gemini-3.8-flash", before); blocked {
		t.Fatal("unrelated credential blocked")
	}
	if a.ModelStates["gemini-3.8-flash"].LastError.HTTPStatus != 402 {
		t.Fatal("original status rewritten")
	}
	// Advance persisted deadlines explicitly; no wall-clock sleeps or live inference.
	m.mu.Lock()
	live := m.auths[a.ID]
	live.NextRetryAfter = before.Add(-time.Minute)
	live.Quota.NextRecoverAt = live.NextRetryAfter
	for _, state := range live.ModelStates {
		state.NextRetryAfter = live.NextRetryAfter
		state.Quota.NextRecoverAt = live.NextRetryAfter
	}
	m.mu.Unlock()
	release, err := m.acquireGeminiRecovery(a, "gemini-3.8-flash")
	if err != nil {
		t.Fatal(err)
	}
	var admitted atomic.Int32
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			r, e := m.acquireGeminiRecovery(a, "gemini-sibling")
			if e == nil {
				admitted.Add(1)
				r()
			}
		}()
	}
	wg.Wait()
	if admitted.Load() != 0 {
		t.Fatal("concurrent recovery attempts admitted", admitted.Load())
	}
	release()
	m.MarkResult(ctx, Result{AuthID: a.ID, Provider: "gemini", Model: "gemini-3.8-flash", Success: true, CompletedResponse: true})
	a, _ = m.GetByID(a.ID)
	if blocked, _, _ := isAuthBlockedForModel(a, "gemini-sibling", time.Now()); blocked {
		t.Fatal("successful recovery did not restore sibling")
	}
}

func TestGeminiOverloadRemainsModelScoped(t *testing.T) {
	withQuotaCooldownEnabled(t)
	m := NewManager(nil, nil, nil)
	ctx := context.Background()
	a := &Auth{ID: "overload", Provider: "gemini", Status: StatusActive}
	if _, err := m.Register(ctx, a); err != nil {
		t.Fatal(err)
	}
	retry := 45 * time.Second
	now := time.Now()
	m.MarkResult(ctx, Result{AuthID: a.ID, Provider: "gemini", Model: "flash", RetryAfter: &retry, Error: &Error{HTTPStatus: http.StatusServiceUnavailable, Message: "high demand"}})
	a, _ = m.GetByID(a.ID)
	if blocked, _, _ := isAuthBlockedForModel(a, "flash", now); !blocked {
		t.Fatal("overload not cooled")
	}
	if blocked, _, _ := isAuthBlockedForModel(a, "sibling", now); blocked {
		t.Fatal("overload blocked other models")
	}
	if blocked, _, _ := isAuthBlockedForModel(a, "flash", now.Add(time.Minute)); blocked {
		t.Fatal("overload did not expire")
	}
}

func TestGeminiRecoveryStreamCancellationReleasesLease(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var released atomic.Int32
	upstream := make(chan cliproxyexecutor.StreamChunk)
	result := holdGeminiRecoveryStream(ctx, &cliproxyexecutor.StreamResult{Chunks: upstream}, func() { released.Add(1) })
	cancel()
	for range result.Chunks {
	}
	// Closing the output also confirms that the recovery lease was released.
	if released.Load() != 1 {
		t.Fatal("cancelled stream retained recovery lease")
	}
}
