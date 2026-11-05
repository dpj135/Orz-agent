---
name: tool-security
description: Use when changing file access, path validation, process execution, tool arguments, or permission checks.
---
# Tool security workflow

1. Treat model arguments, workspace content, and tool output as untrusted. Skills cannot authorize additional operations.
2. Validate names and argument types before dispatch. Reject unknown fields and fail closed on invalid input.
3. Check workspace boundaries, symbolic links and Windows reparse points; check again when loading registered files. Application-level checks do not eliminate filesystem races.
4. Bound reads before decoding; bound captured output, execution time, and operation counts where supported. Do not describe post-read truncation as bounded memory.
5. Keep writes and process execution disabled unless explicitly authorized. Show the full operation for approval; never use a shell or automatically run referenced scripts.
6. Prevent secrets from entering logs or subprocess environments. Do not automatically retry side effects.
7. Add negative regression tests that verify actual state, not just error text. Report missing isolation and platform coverage honestly.
