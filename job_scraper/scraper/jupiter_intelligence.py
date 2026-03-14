from job_scraper.scraper._lever import scrape_board

scrape = scrape_board("jupiterintel", name="Jupiter Intelligence")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
