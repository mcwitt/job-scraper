from job_scraper.scraper._greenhouse import scrape_board

scrape = scrape_board("fivetran", name="Fivetran")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
