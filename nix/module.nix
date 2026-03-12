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
  boardsFile = tomlFormat.generate "boards.toml" cfg.boards;

  # Generate per-user config files in the Nix store
  userFiles =
    name: ucfg:
    let
      preferencesFile = pkgs.writeText "${name}-preferences.md" ucfg.preferences;
      resumeFile = pkgs.writeText "${name}-resume.md" ucfg.resume;
      keywordsFile = pkgs.writeText "${name}-keywords.txt" ucfg.keywords;
    in
    {
      inherit preferencesFile resumeFile keywordsFile;
    };

  stateDir = "/var/lib/job-scraper";

  # Build the main script that runs scrape then per-user scoring
  runScript = pkgs.writeShellScript "job-scraper-run" (
    let
      s = cfg.settings;
      scrapeCmd = concatStringsSep " " [
        "${pkg}/bin/job-scraper"
        "--scrape-only"
        "--status-report"
        "--boards ${boardsFile}"
        "--cache-dir ${stateDir}/cache"
        "--output-dir ${stateDir}/output"
        "--scrape-ttl ${toString s.scrapeTtl}"
        "--max-concurrent ${toString s.maxConcurrent}"
      ];
      userCmds = concatStringsSep "\n" (
        mapAttrsToList (
          name: ucfg:
          let
            files = userFiles name ucfg;
            userDir = "${stateDir}/users/${ucfg.id}";
            cmd = concatStringsSep " " (
              [
                "${pkg}/bin/job-scraper"
                "--input-jobs ${stateDir}/output/jobs_raw.jsonl"
                "--report"
                "--preferences ${files.preferencesFile}"
                "--resume ${files.resumeFile}"
                "--keywords ${files.keywordsFile}"
                "--cache-dir ${userDir}/cache"
                "--output-dir ${userDir}/output"
                "--model ${s.model}"
                "--batch-size ${toString s.batchSize}"
                "--top-k ${toString s.topK}"
                "--dedup-fields ${s.dedupFields}"
              ]
              ++ lib.optional (s.companiesDir != null) "--companies ${s.companiesDir}"
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
      mkdir -p "${stateDir}/cache" "${stateDir}/output"
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

    boards = mkOption {
      type = tomlFormat.type;
      default = { };
      description = ''
        Structured boards config, converted to TOML.
        Example: { greenhouse = [{ board = "anthropic"; name = "Anthropic"; }]; }
      '';
    };

    settings = {
      companiesDir = mkOption {
        type = types.nullOr types.path;
        default = null;
        description = ''
          Path or derivation containing company context markdown files.
          Files should be named by canonical company name (e.g. nvidia.md).
        '';
      };
      model = mkOption {
        type = types.str;
        default = "claude-haiku-4-5-20251001";
        description = "Claude model for scoring.";
      };
      batchSize = mkOption {
        type = types.int;
        default = 20;
        description = "Scoring batch size.";
      };
      maxConcurrent = mkOption {
        type = types.int;
        default = 20;
        description = "Max concurrent HTTP requests.";
      };
      scrapeTtl = mkOption {
        type = types.int;
        default = 86400;
        description = "Scrape cache TTL in seconds.";
      };
      topK = mkOption {
        type = types.int;
        default = 100;
        description = "Keep at most K jobs by relevance.";
      };
      dedupFields = mkOption {
        type = types.str;
        default = "title,company,team";
        description = "Comma-separated Job fields for deduplication.";
      };
    };

    outputDir = mkOption {
      type = types.str;
      readOnly = true;
      default = "${stateDir}/output";
      description = ''
        Read-only. Shared output directory containing jobs_raw.jsonl
        and status.html. Use this to configure downstream services
        (e.g. nginx).
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
                description = "FTS5 query content for relevance filtering.";
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
