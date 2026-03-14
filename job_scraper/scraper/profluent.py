from job_scraper.scraper._greenhouse import scrape_board

scrape = scrape_board("profluent", name="Profluent Bio")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
