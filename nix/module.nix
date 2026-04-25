flake:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (lib)
    mkEnableOption
    mkOption
    mkIf
    types
    mapAttrsToList
    concatStringsSep
    ;
  cfg = config.services.job-scraper;
  pkg = cfg.package;
  tomlFormat = pkgs.formats.toml { };
  configFile = tomlFormat.generate "scrape.toml" cfg.scrape;

  # Strip # comments and blank lines from a keywords string,
  # producing a single FTS5 expression.
  parseKeywords =
    text:
    concatStringsSep " " (
      builtins.filter (s: s != "") (
        map (
          line:
          let
            stripped = lib.trim line;
          in
          if stripped == "" || lib.hasPrefix "#" stripped then "" else stripped
        ) (lib.splitString "\n" text)
      )
    );

  # Generate per-user config files in the Nix store
  userFiles =
    name: ucfg:
    let
      preferencesFile = pkgs.writeText "${name}-preferences.md" ucfg.preferences;
      resumeFile = pkgs.writeText "${name}-resume.md" ucfg.resume;
    in
    {
      inherit preferencesFile resumeFile;
    };

  stateDir = "/var/lib/job-scraper";

  # Build the main script that runs scrape then per-user scoring
  runScript = pkgs.writeShellScript "job-scraper-run" (
    let
      s = cfg.settings;
      scrapeCmd = concatStringsSep " " [
        "${pkg}/bin/job-scraper"
        "run"
        "--config ${configFile}"
        "--scrape-only"
        "--status-report"
        "--cache-dir ${stateDir}/cache"
        "--output-dir ${stateDir}/output"
        "--state-dir ${stateDir}/state"
        "--scrape-ttl ${toString s.scrapeTtl}"
        "--retain-for-seconds ${toString s.retainForSeconds}"
        "--max-concurrent ${toString s.maxConcurrent}"
      ];
      userCmds = concatStringsSep "\n" (
        mapAttrsToList (
          name: ucfg:
          let
            files = userFiles name ucfg;
            userDir = "${stateDir}/users/${ucfg.id}";
            forceScoreFilter = lib.concatMapStringsSep " OR " (f: "(${f})") (
              builtins.filter (f: f != "") ucfg.forceScoreFilters
            );
            cmd = concatStringsSep " " (
              [
                "${pkg}/bin/job-scraper"
                "run"
                "--config ${configFile}"
                "--input-jobs ${stateDir}/state/jobs_store.jsonl"
                "--report"
                "--preferences ${files.preferencesFile}"
                "--resume ${files.resumeFile}"
                "--keywords"
                "'${parseKeywords ucfg.keywords}'"
                "--cache-dir ${userDir}/cache"
                "--output-dir ${userDir}/output"
                "--model ${s.model}"
                "--prep-model ${s.prepModel}"
                "--dedup-fields ${s.dedupFields}"
                "--warn-after-seconds ${toString s.warnAfterSeconds}"
                "--max-concurrent-api ${toString s.maxConcurrentApi}"
                "--init-num-exploit ${toString s.initNumExploit}"
                "--num-explore ${toString s.numExplore}"
                "--num-exploit ${toString s.numExploit}"
                "--init-learning-iters ${toString s.initLearningIters}"
                "--learning-iters ${toString s.learningIters}"
              ]
              ++ lib.optionals (forceScoreFilter != "") [
                "--force-score-keywords"
                "'${forceScoreFilter}'"
              ]
              ++ lib.optional (cfg.companiesDir != null) "--companies-dir ${cfg.companiesDir}"
              ++ lib.optional (
                ucfg.linkedinConnectionsDir != null
              ) "--linkedin-dir ${ucfg.linkedinConnectionsDir}"
            );
          in
          ''
            (
              mkdir -p "${userDir}/cache" "${userDir}/output"
              echo "Scoring for user: ${name}"
              ${cmd}
            ) &
          ''
        ) cfg.users
      );
    in
    ''
      set -euo pipefail
      mkdir -p "${stateDir}/cache" "${stateDir}/output" "${stateDir}/state"
      echo "Starting scrape phase"
      ${scrapeCmd}
      echo "Scrape complete, starting per-user scoring"
      ${userCmds}
      wait
      echo "All done"
    ''
  );
in
{
  options.services.job-scraper = {
    enable = mkEnableOption "job-scraper service";

    package = mkOption {
      type = types.package;
      default = flake.packages.${pkgs.system}.default;
      description = "The job-scraper package to use.";
    };

    scrape = mkOption {
      type = tomlFormat.type;
      default = { };
      description = ''
        Scraper configuration, generates scrape.toml.
        Structure mirrors the TOML schema: `boards.<platform>.<slug>`
        and `custom.<name>`.
      '';
    };

    companiesDir = mkOption {
      type = types.nullOr types.path;
      default = null;
      description = "Directory of company context .md files for scoring.";
    };

    schedule = mkOption {
      type = types.str;
      default = "daily";
      description = "systemd OnCalendar schedule for the scrape/score run.";
    };

    anthropicApiKeyFile = mkOption {
      type = types.path;
      description = ''
        Path to a file containing the Anthropic API key
        (e.g. ANTHROPIC_API_KEY=sk-...).
      '';
    };

    settings = {
      model = mkOption {
        type = types.str;
        default = "claude-haiku-4-5";
        description = "Claude model for scoring.";
      };
      prepModel = mkOption {
        type = types.str;
        default = "claude-sonnet-4-6";
        description = "Claude model for prep generation.";
      };
      maxConcurrent = mkOption {
        type = types.int;
        default = 20;
        description = "Max concurrent HTTP requests.";
      };
      maxConcurrentApi = mkOption {
        type = types.int;
        default = 10;
        description = "Max concurrent Claude API requests.";
      };
      scrapeTtl = mkOption {
        type = types.int;
        default = 86400;
        description = "Scrape cache TTL in seconds.";
      };
      retainForSeconds = mkOption {
        type = types.int;
        default = 604800;
        description = ''
          Carry forward unobserved jobs for this many seconds
          before evicting them from the store. 0 disables
          retention.
        '';
      };
      warnAfterSeconds = mkOption {
        type = types.int;
        default = 172800;
        description = ''
          Flag jobs in the report as stale (⚠️) if not observed
          within this many seconds.
        '';
      };
      dedupFields = mkOption {
        type = types.str;
        default = "title,company,team,description";
        description = "Comma-separated Job fields for deduplication.";
      };
      initNumExploit = mkOption {
        type = types.int;
        default = 100;
        description = "Jobs to seed by similarity for cold start.";
      };
      numExplore = mkOption {
        type = types.int;
        default = 10;
        description = "Jobs to explore per active learning iteration.";
      };
      numExploit = mkOption {
        type = types.int;
        default = 10;
        description = "Jobs to exploit per active learning iteration.";
      };
      initLearningIters = mkOption {
        type = types.int;
        default = 20;
        description = "Active learning iterations during cold start.";
      };
      learningIters = mkOption {
        type = types.int;
        default = 1;
        description = "Active learning iterations during warm start.";
      };
    };

    outputDir = mkOption {
      type = types.str;
      readOnly = true;
      default = "${stateDir}/output";
      description = ''
        Read-only. Shared output directory containing status.html
        and scraper_errors.jsonl. Use this to configure downstream
        services (e.g. nginx).
      '';
    };

    users = mkOption {
      type = types.attrsOf (
        types.submodule (
          { name, config, ... }:
          {
            options = {
              id = mkOption {
                type = types.str;
                default = builtins.substring 0 8 (builtins.hashString "sha256" name);
                description = ''
                  Public identifier used in file paths and URLs.
                  Defaults to the first 8 characters of the SHA-256 hash of the user attribute name.
                '';
              };
              preferences = mkOption {
                type = types.str;
                description = "Candidate job preferences (markdown content).";
              };
              resume = mkOption {
                type = types.str;
                description = "Candidate resume (markdown content).";
              };
              keywords = mkOption {
                type = types.str;
                description = "FTS5 query expression for boolean pre-filtering.";
              };
              forceScoreFilters = mkOption {
                type = types.listOf types.str;
                default = [ ];
                description = ''
                  FTS5 query expressions for jobs that should always be
                  LLM-scored, regardless of which jobs the active-learning
                  loop selects. Entries are OR'd together and passed as
                  a single `--force-score-keywords` argument to `run`,
                  bypassing the per-user `keywords` pre-filter. Useful
                  for forcing scoring of jobs at specific companies
                  you're particularly interested in.
                '';
                example = [
                  "company:stripe"
                  "company:anthropic AND title:engineer"
                ];
              };
              linkedinConnectionsDir = mkOption {
                type = types.nullOr types.path;
                default = null;
                description = "Path or derivation containing LinkedIn data (Connections.csv and network/).";
              };
              outputDir = mkOption {
                type = types.str;
                readOnly = true;
                default = "${stateDir}/users/${config.id}/output";
                description = ''
                  Read-only. Directory where this user's report is generated.
                  Use this to configure downstream services (e.g. nginx).
                '';
              };
            };
          }
        )
      );
      default = { };
      description = "Per-user scoring configuration.";
    };
  };

  config = mkIf cfg.enable {
    users.users.job-scraper = {
      isSystemUser = true;
      group = "job-scraper";
      home = stateDir;
    };
    users.groups.job-scraper = { };

    systemd.services.job-scraper = {
      description = "Job scraper: scrape and score job postings";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = runScript;
        EnvironmentFile = cfg.anthropicApiKeyFile;
        User = "job-scraper";
        Group = "job-scraper";
        StateDirectory = "job-scraper";
        StateDirectoryMode = "0775";
        UMask = "0002";
        # Hardening
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        ReadWritePaths = [ stateDir ];
      };
    };

    systemd.timers.job-scraper = {
      description = "Timer for job-scraper service";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = cfg.schedule;
        Persistent = true;
      };
    };
  };
}
