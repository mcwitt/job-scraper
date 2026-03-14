from job_scraper.scraper._greenhouse import scrape_board

scrape = scrape_board("thealleninstitute", name="AI2")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
