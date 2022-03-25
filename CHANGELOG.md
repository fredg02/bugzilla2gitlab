# Changelog

## Changes compared to upstream since tag [upstream_2022-01-06](https://github.com/fredg02/bugzilla2gitlab/tree/upstream_2022-01-06)

### New features

* Fetch Bugzilla bugs
* Support fetching a list of Bugzilla components
* Support closing of migrated Bugzilla issue
* Improve attachment handling
  * Deal with attachments marked as obsolete
  * Use strikethrough and add a note, when attachments have been deleted
* Add Dockerfile and basic instructions
* Add test mode to read XML from files

### Improvments

* Improve links
  * Find bug links "[b|B]ug 12345" and replace with markdown links
  * Handle "bug 12345" and "bug 12345 comment [#]12" links gracefully
  * Set bug links in (extended) description
* Comments
  * Show reporter and commenter name (not only email)
  * Add new option whether datetime should be shown in every comment
  * Add option "show_email" to decide whether emails are shown or not
* Description
  * Make showing version, os, architecture in issue description optional
  * Show "Importance" in description table (priority + severity)
  * Add link to duplicate bug in bug status field in description
* Labels
  * Automatically create labels for imported components
  * Set label for critical/blocker severity (can be configured)
  * Turn keyword "spam" in whiteboard_status field into "spam" label
* Bug relations
  * Support "see also" relation
  * Support for "Gerrit" and "Git commit" "see also" links
* Misc
  * Lookup GitLab users by email
  * Specify target GitLab project by name, not (only) by ID
  * Mark issues as confidential when they belong to a certain group
  * Add option to use Bugzilla id in GitLab issue title
  * Support unassign list (file name "unassign_users" in config dir)
  * defaults.yml: put options into sections
  * Add log output to dry-run

### Bugfixes

* Improve the formatting of text in Markdown syntax
  * Adding extra newlines were required
  * Fix newlines for numbered lists
  * Handle white spaces after bug links correctly
  * Escape hashtags
  * Replace `comment #5` with `comment 5` to avoid links to wrong issues
* Fix wrong bug IDs in links for dependent and blocking bugs
* Avoid "By <user>" comment, when Bugzilla misc user is commenting
* Deal gracefully with empty descriptions
* Fix typos
  
### Changes

* Use "Description" instead of "Extended Description"
* Always show modification time
* Removed "Resolved" field (it shows the same date as "Modified")
* "Depends On" => "Depends on", "Blocked by" => "Blocks"
* Show version in description table only if it's not "unspecified"