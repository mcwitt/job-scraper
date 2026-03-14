from job_scraper.scraper._workday import scrape_board

scrape = scrape_board(
    "blackrock", "wd1", "BlackRock_Professional", name="BlackRock"
)

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
