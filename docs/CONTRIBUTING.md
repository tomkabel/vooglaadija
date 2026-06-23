# Contributing

## Development Workflow

1. Fork the repository.
1. Create a branch from `main`:

- `feature/<short-description>` — new features
- `fix/<short-description>` — bug fixes
- `hotfix/<short-description>` — critical production fixes

1. Commit changes following [Conventional Commits](https://www.conventionalcommits.org/).
1. Push to your fork and open a Pull Request.
1. Ensure CI checks pass before requesting review.

Direct commits to `main` are prohibited.

## Code Standards

- Type hints are required for all new functions and methods.
- Follow PEP 8 with a 100-character line limit.
- Use `async`/`await` for I/O-bound operations.
- Write tests for new features and bug fixes.

## Running Tests

This project uses [Hatch](https://hatch.pypa.io/) as the environment manager. Commands use Hatch's
`env:script` syntax (`hatch run <env>:<script>`).

```bash
# Unit tests only
hatch run test:unit

# Integration tests only
hatch run test:integration

# All tests with coverage
hatch run test:all

# HTML coverage report
hatch run test:cov-html

# Linting
hatch run lint:check
hatch run lint:format-check

# Type checking
hatch run type:check

# Security scan
hatch run security:scan-bandit
```

## Manual Test Checklist

Before submitting or demoing, verify:

- [ ] Register new user works
- [ ] Login with wrong password shows error
- [ ] Login with correct password redirects to dashboard
- [ ] Create download with invalid URL shows error
- [ ] Create download with valid URL appears in list
- [ ] SSE status updates during processing
- [ ] Completed file downloads successfully
- [ ] Job retry works for failed jobs
- [ ] Update username in settings works
- [ ] Change password works
- [ ] Delete account removes user and all files
- [ ] Logout redirects to login
