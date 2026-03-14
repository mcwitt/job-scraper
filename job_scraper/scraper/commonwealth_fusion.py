from job_scraper.scraper._lever import scrape_board

scrape = scrape_board("cfsenergy", name="Commonwealth Fusion Systems")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
