## Tour

- A line starting with a dash is a command, e.g. `-help`. Tab completes command names.
- Anything else is a conversation with me.
- `-help` lists the commands, `-help <command>` shows its options.
- `-settings` shows and changes settings (language, provider, model).
- `-setup` runs the model setup again.
- `-tour` shows this tour any time.
- `-exit` or Ctrl-D quits. Ctrl-C cancels the current line or the running command.
- Commands also run without the chat: `sortingcap run <command>`, e.g. from cron. `setup` is chat-only.

## Next steps

- For now I mostly chat. Coming versions will learn who you are (`-add-information`), go through job listings (`-scrape-jobs`) and write letters.
