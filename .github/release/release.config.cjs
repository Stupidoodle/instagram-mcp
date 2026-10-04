// The semantic-release config of the release pipeline. Source: skoggi-ch
// infrastructure/platform/release/release.config.cjs, copied into each repo's
// .github/release/ by onboard.sh; edit it there. release.yml loads it for each run, and
// backfill.mjs takes the analyzer and notes options from it, so backfilled notes read
// the same as live ones. No npm publish, no commit back, no CHANGELOG.md: the notes live
// in the GitHub Release and on the changelog site.

// Only these footers start a note; a trailer never does, so it never bumps major.
const parserOpts = { noteKeywords: ["BREAKING CHANGE", "BREAKING-CHANGE"] };

// A BREAKING CHANGE note runs to the end of the message, so the commit trailers below it
// would be printed with it.
const trailer = /^\s*(co-authored-by|claude-session|signed-off-by):/i;
const withoutTrailers = (text) =>
  String(text ?? "").split("\n").filter((line) => !trailer.test(line)).join("\n").trim();

module.exports = {
  branches: ["main"],
  tagFormat: "v${version}",
  plugins: [
    ["@semantic-release/commit-analyzer", { preset: "conventionalcommits", parserOpts }],
    ["@semantic-release/release-notes-generator", {
      preset: "conventionalcommits",
      parserOpts,
      writerOpts: {
        finalizeContext: (context) => {
          for (const group of context.noteGroups ?? []) {
            for (const note of group.notes) note.text = withoutTrailers(note.text);
          }
          return context;
        },
      },
    }],
    ["@semantic-release/github", {
      successComment: false,
      failComment: false,
      failTitle: false,
      releasedLabels: false,
    }],
  ],
};
