# D-ID Avatar Provider

## 1. Get a D-ID API key

1. Create/sign in to your D-ID account: https://studio.d-id.com/
2. Open the **Developer/API** section.
3. Create/copy your API key.
4. Create:

```text
configs/providers/avatar_render.yaml
```

Add:

```yaml
api_key: "YOUR_DID_API_KEY"
base_url: "https://api.d-id.com"
poll_interval_seconds: 5
poll_timeout_seconds: 300
```

## 2. Add smoke-test inputs

Place the test files at:

```text
fixtures/stubs/mona_lisa.png
fixtures/stubs/voice_run_20260810_151321_851f694f20e75967989ad4eb580b443f15f0020dbafa12d370f4135bed639365.mp3
```

The smoke test uploads these files to MinIO first. The provider then uploads them to D-ID's temporary storage, uses the returned URLs to create the D-ID talk, downloads the generated MP4, and stores the final video in MinIO.

---

## 3. Run the provider smoke test

From the repository root:

```powershell
uv run python tests/test_did_avatar_provider.py
```

A successful run ends with:

```text
✓ D-ID provider smoke test passed.
```

The generated video is stored in MinIO as an `avatar_*` artifact, and the test prints its artifact path and SHA-256 hash.

---

## 4. Troubleshooting

If the request times out while uploading to D-ID, retry the smoke test. The D-ID API must be reachable from the machine running the test.

Check connectivity with:

```powershell
Test-NetConnection api.d-id.com -Port 443
```

A successful result should show:

```text
TcpTestSucceeded : True
```

