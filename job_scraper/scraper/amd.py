from job_scraper.scraper._icims import scrape_board

scrape = scrape_board("careers.amd.com", name="AMD")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
