from job_scraper.scraper._phenom import scrape_board

scrape = scrape_board("careers.thredup.com", name="thredUP")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
